"""
Search: parse the query, run the retrievers, fuse, enrich.

Replaces search_engine.py, which searched a `shots` table that no longer exists, ordered by an expression pgvector could not index, and sorted NULLs first.
"""

import time
from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device
from retrieval import fusion, query_parser, retrievers

SELECT_MODEL = "SELECT model_id, name, dims FROM embedding_model WHERE name = %s;"

# One round trip for everything the UI needs about the fused scenes.
ENRICH = """
    SELECT s.scene_id, s.video_id, s.shot_index, s.start_frame, s.end_frame, s.start_ms, s.end_ms,
           v.fps, v.duration_ms, v.damaged,
           k.keyframe_id, k.kf_index, k.frame_number, k.ts_ms
    FROM scenes s
             JOIN videos v ON v.video_id = s.video_id
             LEFT JOIN LATERAL (
        SELECT kk.keyframe_id, kk.kf_index, kk.frame_number, kk.ts_ms
        FROM keyframes kk
        WHERE kk.scene_id = s.scene_id
        ORDER BY (kk.keyframe_id = ANY (%(preferred)s)) DESC, kk.kf_index
        LIMIT 1) k ON TRUE
    WHERE s.scene_id = ANY (%(scene_ids)s);
"""


@dataclass
class SearchResult:
    results: list
    timings: dict
    signals_used: list


class SearchEngine:
    """
    Holds the query-side models.

    Only the SigLIP2 *text* tower is loaded. The old code loaded the full AutoModel, so the vision
    tower -- some 2 GB, and useless once the collection is embedded -- sat in RAM.
    """

    def __init__(self, conn):
        self.conf = Config()
        self.logger = custom_logger.get_logger("search")
        self.conn = conn
        self.device = device.pick_device()
        self.visual_model = None
        self.text_model = None
        # collection -> loaded visual model spec, for collections SigLIP2 does not serve well.
        self.collection_models = {}
        self._load_models()

    def _resolve(self, name):
        with self.conn.cursor() as cur:
            cur.execute(SELECT_MODEL, (name,))
            return cur.fetchone()

    def _load_visual(self, model_name):
        """Loads a visual model's text tower, or None when it is not registered."""
        from transformers import AutoModel, AutoProcessor

        registered = self._resolve(model_name)
        if not registered:
            return None
        model_id, name, dims = registered
        processor = AutoProcessor.from_pretrained(name)
        full = AutoModel.from_pretrained(name, dtype=device.pick_dtype(self.device))
        text_tower = full.text_model.to(self.device).eval()
        # The projection head lives outside text_model on SigLIP, so keep whichever exists.
        head = getattr(full, "text_projection", None)
        del full
        self.logger.info(f"Loaded {name} text tower ({dims}d) on {self.device}")
        return {"model_id": model_id, "name": name, "dims": dims,
                "processor": processor, "tower": text_tower, "head": head}

    def visual_model_for(self, collection):
        """
        Which visual model answers for a collection.

        Vector spaces are not comparable, so a query must be embedded by the same model that
        embedded the frames it is being compared against. Scoping a search to a collection is
        therefore also choosing a model.
        """
        if collection:
            return self.collection_models.get(collection.upper(), self.visual_model)
        return self.visual_model

    def _load_models(self):
        from transformers import AutoModel, AutoProcessor, AutoTokenizer

        self.visual_model = self._load_visual(self.conf.KIS_MODEL_NAME)

        # Domain models, one per collection that needs one. Only loaded when the model is both
        # configured and actually registered with embeddings -- otherwise a typo in the env var
        # would silently route a collection at a model that has no vectors, and every search of it
        # would come back empty rather than merely mediocre.
        for collection, model_name in self.conf.collection_models().items():
            if model_name == self.conf.KIS_MODEL_NAME:
                continue
            spec = self._load_visual(model_name)
            if spec:
                self.collection_models[collection.upper()] = spec
                self.logger.info(f"{collection}: routed to {model_name}")
            else:
                self.logger.warning(
                    f"{collection}: '{model_name}' is not registered in embedding_model, "
                    f"falling back to {self.conf.KIS_MODEL_NAME}")

        text = self._resolve(self.conf.TEXT_MODEL)
        if text:
            model_id, name, dims = text
            tokenizer = AutoTokenizer.from_pretrained(name)
            model = AutoModel.from_pretrained(name, dtype=device.pick_dtype(self.device))
            self.text_model = {"model_id": model_id, "dims": dims, "tokenizer": tokenizer,
                               "model": model.to(self.device).eval()}
            self.logger.info(f"Loaded {name} ({dims}d) on {self.device}")

    @property
    def ready(self) -> bool:
        return self.visual_model is not None

    def embed_query_visual(self, text: str, spec=None) -> str:
        import torch

        spec = spec or self.visual_model
        inputs = spec["processor"](text=[text], return_tensors="pt", padding="max_length",
                                   truncation=True).to(self.device)
        # The processor hands the vision tower's keys over too; the text tower rejects them.
        inputs = {k: v for k, v in inputs.items() if k in ("input_ids", "attention_mask")}
        with torch.no_grad():
            output = spec["tower"](**inputs)
            # transformers 5.x returns an output object here, not a tensor.
            vector = output.pooler_output if hasattr(output, "pooler_output") else output[1]
            if spec["head"] is not None:
                vector = spec["head"](vector)
            vector = vector / vector.norm(p=2, dim=-1, keepdim=True)
        return _to_pgvector(vector[0].float().cpu().numpy())

    def embed_query_text(self, text: str) -> str:
        import torch
        from pipeline.text_embed import as_query

        spec = self.text_model
        inputs = spec["tokenizer"]([as_query(text)], padding=True, truncation=True,
                                   max_length=self.conf.TEXT_MAX_TOKENS,
                                   return_tensors="pt").to(self.device)
        with torch.no_grad():
            hidden = spec["model"](**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            pooled = pooled / pooled.norm(p=2, dim=-1, keepdim=True)
        return _to_pgvector(pooled[0].float().cpu().numpy())

    def search(self, query: str, exclude=None, top_k=None, collection=None,
               weights=None, depth=None) -> SearchResult:
        conf = self.conf
        parsed = query_parser.parse(query)
        exclude = list(dict.fromkeys((exclude or []) + parsed.exclude_videos))
        top_k = top_k or conf.SEARCH_TOP_K
        depth = depth or conf.RETRIEVER_DEPTH
        weights = {**conf.rrf_weights(), **(weights or {})}
        collection = normalize_collection(collection)

        lists, timings = {}, {}

        def run(name, fn):
            started = time.monotonic()
            try:
                lists[name] = fn()
            except Exception as e:
                self.logger.error(f"retriever '{name}' failed: {e}")
                lists[name] = []
            timings[name] = round((time.monotonic() - started) * 1000, 1)

        # Visual first, so its keyframe is the one shown -- it is the frame that actually matched,
        # where OCR and ASR only identify the scene.
        visual_spec = self.visual_model_for(collection)
        if parsed.has_free_text and visual_spec:
            vector = self.embed_query_visual(parsed.free_text, visual_spec)
            run("visual", lambda: retrievers.visual(
                self.conn, vector, visual_spec["model_id"], visual_spec["dims"],
                exclude, collection, depth))

            tokens = query_parser.distinctive_tokens(parsed.free_text)
            if tokens:
                run("ocr", lambda: retrievers.ocr_lexical(
                    self.conn, tokens, exclude, collection, depth))

            if self.text_model:
                text_vector = self.embed_query_text(parsed.free_text)
                run("caption", lambda: retrievers.caption(
                    self.conn, text_vector, self.text_model["model_id"],
                    self.text_model["dims"], exclude, collection, depth))
                run("transcript", lambda: retrievers.transcript(
                    self.conn, text_vector, self.text_model["model_id"],
                    self.text_model["dims"], exclude, collection, depth))

        if parsed.ocr_phrase:
            run("ocr_phrase", lambda: retrievers.ocr_phrase(
                self.conn, parsed.ocr_phrase, exclude, collection, depth))
        if parsed.asr_phrase:
            run("asr_phrase", lambda: retrievers.transcript_phrase(
                self.conn, parsed.asr_phrase, exclude, collection, depth))

        fused = fusion.fuse(lists, weights, limit=top_k)
        enriched = self._enrich(fused)
        return SearchResult(results=enriched, timings=timings,
                            signals_used=[n for n, r in lists.items() if r])

    def similar(self, keyframe_id, exclude=None, top_k=None, collection=None) -> SearchResult:
        """Scenes that look like the given keyframe. Its own scene is dropped from the results."""
        top_k = top_k or self.conf.SEARCH_TOP_K
        collection = normalize_collection(collection)
        started = time.monotonic()
        spec = self.visual_model_for(collection)
        pairs = retrievers.similar_to_keyframe(
            self.conn, keyframe_id, spec["model_id"], spec["dims"],
            exclude or [], collection or None, top_k)
        timings = {"similar": round((time.monotonic() - started) * 1000, 1)}

        fused = [fusion.FusedResult(scene_id=scene_id, score=1.0 / (rank + 1),
                                    signals={"similar": rank + 1}, keyframe_id=kf)
                 for rank, (scene_id, kf) in enumerate(pairs) if kf != keyframe_id][:top_k]
        return SearchResult(results=self._enrich(fused), timings=timings,
                            signals_used=["similar"])

    def _enrich(self, fused):
        if not fused:
            return []
        scene_ids = [f.scene_id for f in fused]
        preferred = [f.keyframe_id for f in fused if f.keyframe_id is not None]

        with self.conn.cursor() as cur:
            cur.execute(ENRICH, {"scene_ids": scene_ids, "preferred": preferred or [-1]})
            rows = {r[0]: r for r in cur.fetchall()}

        out = []
        for item in fused:
            row = rows.get(item.scene_id)
            if row is None:
                continue
            (scene_id, video_id, shot_index, start_frame, end_frame, start_ms, end_ms,
             fps, duration_ms, damaged, keyframe_id, kf_index, frame_number, ts_ms) = row
            out.append({
                "scene_id": scene_id,
                "keyframe_id": keyframe_id,
                "video_id": video_id,
                "shot_index": shot_index,
                "kf_index": kf_index,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "frame_number": frame_number,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "ts_ms": ts_ms,
                "fps": fps,
                "damaged": damaged,
                "score": round(item.score, 6),
                "signals": item.signals,
            })
        return out


def normalize_collection(collection):
    """Collections are stored uppercase (V3C1, MVK, GYNSURG). None means every collection."""
    return collection.strip().upper() if collection else None


def _to_pgvector(values) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"

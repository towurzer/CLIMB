"""
Search: parse the query, run the retrievers, fuse, enrich.

Replaces search_engine.py, which searched a `shots` table that no longer exists, ordered by an expression pgvector could not index, and sorted NULLs first.
"""

import functools
import time
from collections import OrderedDict
from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device
from retrieval import fusion, query_parser, retrievers, temporal

SELECT_MODEL = "SELECT model_id, name, dims FROM embedding_model WHERE name = %s;"

# The sources a caller can switch off
SOURCE_RETRIEVERS = {
    "visual": ("visual",),
    "ocr": ("ocr",),
    "asr": ("transcript",),
    "caption": ("caption",),
}


def _allowed_retrievers(sources):
    """
    Retriever names a request permits, or None for "no restriction".

    An unknown source name contributes nothing rather than raising: the frontend and this map are
    edited in different languages, and a typo should narrow a search, never break it.
    """
    if not sources:
        return None
    allowed = set()
    for source in sources:
        allowed.update(SOURCE_RETRIEVERS.get(str(source).strip().lower(), ()))
    return allowed


def _ends_transaction(method):
    """
    Ends the transaction after every public query.

    The engine holds one connection for the life of the worker process and psycopg2 opens a
    transaction on the first statement, so without this the worker sits `idle in transaction`
    forever. (found a 7h39m-old transaction from a single search xd)

    Rollback rather than commit: every path through here is read-only, so there is never anything
    to commit, and rollback is the cheaper and more honest terminator.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            try:
                self.conn.rollback()
            except Exception:  # a dead connection is the next query's problem, not this one's
                logger = custom_logger.get_logger("engine")
                logger.warning("rollback after query failed; connection may be dead")

    return wrapper

# One round trip for everything the UI needs about the fused keyframes.
#
# Keyed on keyframe, not scene: results are one row per keyframe now, so the row *is* the frame
# that matched. `strip` still carries the whole scene, because the panel under the player lets the
# operator scrub the rest of the shot without going back to browse.
ENRICH = """
    SELECT k.keyframe_id, k.kf_index, k.frame_number, k.ts_ms,
           s.scene_id, s.video_id, s.shot_index, s.start_frame, s.end_frame, s.start_ms, s.end_ms,
           v.fps, v.duration_ms, v.damaged,
           strip.keyframes
    FROM keyframes k
             JOIN scenes s ON s.scene_id = k.scene_id
             JOIN videos v ON v.video_id = s.video_id
             LEFT JOIN LATERAL (
        SELECT json_agg(json_build_object(
                'keyframe_id', kk.keyframe_id,
                'kf_index', kk.kf_index,
                'frame_number', kk.frame_number,
                'ts_ms', kk.ts_ms) ORDER BY kk.ts_ms) AS keyframes
        FROM keyframes kk
        WHERE kk.scene_id = s.scene_id) strip ON TRUE
    WHERE k.keyframe_id = ANY (%(keyframe_ids)s);
"""

# Everything temporal.link needs from a stage hit, and nothing else.
#
# A stage runs 1000 deep but only the handful of hits that end up inside a chain are ever shown, so
# enriching a whole stage is work thrown away: linking reads video_id, ts_ms, scene_id and score,
# never the filmstrip.
LINK_ROWS = """
    SELECT keyframe_id, video_id, ts_ms
    FROM keyframes
    WHERE keyframe_id = ANY (%(keyframe_ids)s);
"""


@dataclass
class SearchResult:
    results: list
    timings: dict
    signals_used: list
    # Present only for a sequence query: what it was understood to mean, and how much each stage
    # found. Without it an empty page cannot be told apart from a stage that matched nothing.
    temporal: dict = None


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
        # (tower, model_id, text) -> pgvector literal, most recently used last. See
        # QUERY_EMBED_CACHE_SIZE: query embedding dominates a search, and refining a sequence
        # changes one stage out of three.
        self._embed_cache = OrderedDict()
        # Whether several query strings go through the towers as one batch.
        #
        # Only worth it where per-call overhead dominates.
        # So on CPU the stages stay serial and the win comes from the cache instead.
        self.batch_embeddings = self.device in ("cuda", "mps")
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
        from transformers import AutoModel, AutoTokenizer

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

    def _cached_embed(self, texts, cache_key, compute) -> list:
        """
        Vectors for `texts`, in order, reusing whatever the cache already holds.

        `compute` embeds a list of texts and returns their pgvector literals. Misses go through it
        in one call on a GPU and one at a time on CPU, see `batch_embeddings`. Repeats within the
        same request are collapsed too, so `sunset >> door >> sunset` embeds two strings, not three.
        """
        vectors = [None] * len(texts)
        pending = {}
        for index, text in enumerate(texts):
            cached = self._embed_cache.get((cache_key, text))
            if cached is not None:
                self._embed_cache.move_to_end((cache_key, text))
                vectors[index] = cached
            else:
                pending.setdefault(text, []).append(index)

        if pending:
            misses = list(pending)
            step = len(misses) if self.batch_embeddings else 1
            computed = []
            for start in range(0, len(misses), step):
                computed += compute(misses[start:start + step])
            for text, vector in zip(misses, computed):
                for index in pending[text]:
                    vectors[index] = vector
                self._remember_embedding((cache_key, text), vector)
        return vectors

    def _remember_embedding(self, key, vector):
        self._embed_cache[key] = vector
        self._embed_cache.move_to_end(key)
        while len(self._embed_cache) > self.conf.QUERY_EMBED_CACHE_SIZE:
            self._embed_cache.popitem(last=False)

    def _run_visual(self, texts, spec) -> list:
        import torch

        inputs = spec["processor"](text=list(texts), return_tensors="pt", padding="max_length",
                                   truncation=True).to(self.device)
        # The processor hands the vision tower's keys over too; the text tower rejects them.
        inputs = {k: v for k, v in inputs.items() if k in ("input_ids", "attention_mask")}
        with torch.no_grad():
            output = spec["tower"](**inputs)
            # transformers 5.x returns an output object here, not a tensor.
            vectors = output.pooler_output if hasattr(output, "pooler_output") else output[1]
            if spec["head"] is not None:
                vectors = spec["head"](vectors)
            vectors = vectors / vectors.norm(p=2, dim=-1, keepdim=True)
        return [_to_pgvector(v) for v in vectors.float().cpu().numpy()]

    def _run_text(self, texts, spec) -> list:
        import torch
        from pipeline.text_embed import as_query

        inputs = spec["tokenizer"]([as_query(t) for t in texts], padding=True, truncation=True,
                                   max_length=self.conf.TEXT_MAX_TOKENS,
                                   return_tensors="pt").to(self.device)
        with torch.no_grad():
            hidden = spec["model"](**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            pooled = pooled / pooled.norm(p=2, dim=-1, keepdim=True)
        return [_to_pgvector(v) for v in pooled.float().cpu().numpy()]

    def embed_query_visual_batch(self, texts, spec=None) -> list:
        spec = spec or self.visual_model
        return self._cached_embed(texts, ("visual", spec["model_id"]),
                                  lambda batch: self._run_visual(batch, spec))

    def embed_query_text_batch(self, texts) -> list:
        spec = self.text_model
        return self._cached_embed(texts, ("text", spec["model_id"]),
                                  lambda batch: self._run_text(batch, spec))

    def embed_query_visual(self, text: str, spec=None) -> str:
        return self.embed_query_visual_batch([text], spec)[0]

    def embed_query_text(self, text: str) -> str:
        return self.embed_query_text_batch([text])[0]

    @_ends_transaction
    def search(self, query: str, exclude=None, top_k=None, collection=None,
               weights=None, depth=None, sources=None) -> SearchResult:
        conf = self.conf
        parsed = query_parser.parse_temporal(
            query, conf.TEMPORAL_DEFAULT_DELTA_MS, conf.TEMPORAL_MAX_DELTA_MS,
            conf.TEMPORAL_MAX_STAGES)
        exclude = list(dict.fromkeys((exclude or []) + parsed.exclude_videos))
        top_k = top_k or conf.SEARCH_TOP_K
        weights = {**conf.rrf_weights(), **(weights or {})}
        collection = normalize_collection(collection)

        allowed = _allowed_retrievers(sources)

        if parsed.is_temporal:
            return self._search_temporal(parsed, exclude, collection, weights, top_k, depth,
                                         allowed)

        fused, timings, lists = self._retrieve(
            parsed.stages[0], exclude, collection, weights,
            depth or conf.RETRIEVER_DEPTH, top_k, allowed)
        return SearchResult(results=self._enrich(fused), timings=timings,
                            signals_used=[n for n, r in lists.items() if r])

    def _retrieve(self, parsed, exclude, collection, weights, depth, limit, allowed=None,
                  vectors=None):
        """
        Run every retriever for one query and fuse them.

        Shared by a plain search and by each stage of a sequence, which is the point: a stage is
        exactly as strong as a search, so `text:"Boulangerie" >> a dog runs past` works and every
        future signal joins the temporal path for free.

        `allowed` is the set of retriever names the request permits, or None for all of them. A
        disabled retriever is never run, so its query embedding is never computed either.

        `vectors` supplies query embeddings already computed by the caller, which is how a sequence
        puts all its stages through the towers in one go; anything missing is embedded here.
        """
        lists, timings = {}, {}
        vectors = vectors or {}

        def enabled(name):
            return allowed is None or name in allowed

        def run(name, fn):
            started = time.monotonic()
            try:
                lists[name] = fn()
            except Exception as e:
                self.logger.error(f"retriever '{name}' failed: {e}")
                lists[name] = []
            timings[name] = round((time.monotonic() - started) * 1000, 1)

        def embed(kind, fn):
            """Embedding is the most expensive part of a search; it belongs in the timings."""
            if kind in vectors:
                return vectors[kind]
            started = time.monotonic()
            vector = fn()
            timings["embed"] = round(timings.get("embed", 0.0)
                                     + (time.monotonic() - started) * 1000, 1)
            return vector

        # Visual first, so its keyframe is the one shown -- it is the frame that actually matched,
        # where OCR and ASR only identify the scene.
        visual_spec = self.visual_model_for(collection)
        if parsed.has_free_text and visual_spec:
            if enabled("visual"):
                vector = embed("visual",
                               lambda: self.embed_query_visual(parsed.free_text, visual_spec))
                run("visual", lambda: retrievers.visual(
                    self.conn, vector, visual_spec["model_id"], visual_spec["dims"],
                    exclude, collection, depth))

            tokens = query_parser.distinctive_tokens(parsed.free_text)
            if tokens and enabled("ocr"):
                run("ocr", lambda: retrievers.ocr_lexical(
                    self.conn, tokens, exclude, collection, depth))

            # One embedding serves both, so it is only worth computing if either is wanted.
            if self.text_model and (enabled("caption") or enabled("transcript")):
                text_vector = embed("text", lambda: self.embed_query_text(parsed.free_text))
                if enabled("caption"):
                    run("caption", lambda: retrievers.caption(
                        self.conn, text_vector, self.text_model["model_id"],
                        self.text_model["dims"], exclude, collection, depth))
                if enabled("transcript"):
                    run("transcript", lambda: retrievers.transcript(
                        self.conn, text_vector, self.text_model["model_id"],
                        self.text_model["dims"], exclude, collection, depth))

        if parsed.ocr_phrase:
            run("ocr_phrase", lambda: retrievers.ocr_phrase(
                self.conn, parsed.ocr_phrase, exclude, collection, depth))
        if parsed.asr_phrase:
            run("asr_phrase", lambda: retrievers.transcript_phrase(
                self.conn, parsed.asr_phrase, exclude, collection, depth))

        return fusion.fuse(lists, weights, limit=limit), timings, lists

    def _embed_stages(self, stages, collection, allowed) -> list:
        """
        Every stage's query vectors, computed before any stage runs.

        Hoisted out of the per-stage loop for two reasons: a GPU can then put all the stages
        through the towers in one pass, and the cache underneath means a refined sequence only pays
        for the stage that actually changed. One dict of vectors per stage, in query order.
        """

        def enabled(name):
            return allowed is None or name in allowed

        visual_spec = self.visual_model_for(collection)
        vectors = [{} for _ in stages]
        # Mirrors the guard in _retrieve: without a visual model no free-text signal runs at all.
        wanted = [i for i, stage in enumerate(stages) if stage.has_free_text] if visual_spec else []
        if not wanted:
            return vectors

        texts = [stages[i].free_text for i in wanted]
        if enabled("visual"):
            for index, vector in zip(wanted, self.embed_query_visual_batch(texts, visual_spec)):
                vectors[index]["visual"] = vector
        if self.text_model and (enabled("caption") or enabled("transcript")):
            for index, vector in zip(wanted, self.embed_query_text_batch(texts)):
                vectors[index]["text"] = vector
        return vectors

    def _search_temporal(self, parsed, exclude, collection, weights, top_k, depth,
                         allowed=None) -> SearchResult:
        """
        Each stage is a full search; the chaining is arithmetic over the results.

        A chain is only found where every stage independently surfaced a hit in the same video,
        which is why stages run deeper than a normal search. That depth is also why nothing is
        enriched until the chains exist: linking needs four columns per hit, and at stage depth
        1000 the enrichment of everything that does *not* survive is the second largest cost in the
        query after embedding.
        """
        conf = self.conf
        stage_depth = depth or conf.TEMPORAL_STAGE_DEPTH

        timings, signals = {}, []
        started = time.monotonic()
        stage_vectors = self._embed_stages(parsed.stages, collection, allowed)
        timings["embed"] = round((time.monotonic() - started) * 1000, 1)

        stage_rows, stage_fused = [], []
        for index, stage in enumerate(parsed.stages):
            fused, stage_timings, lists = self._retrieve(
                stage, exclude, collection, weights, stage_depth, conf.TEMPORAL_STAGE_TOP_K,
                allowed, vectors=stage_vectors[index])
            for name, elapsed in stage_timings.items():
                timings[f"s{index}:{name}"] = elapsed
            signals += [f"s{index}:{name}" for name, results in lists.items() if results]
            # Fusion keys on keyframe, so a keyframe is named at most once per stage.
            stage_fused.append({item.keyframe_id: item for item in fused})
            stage_rows.append(self._link_rows(fused))

        started = time.monotonic()
        chains = temporal.link(stage_rows, parsed.gaps_ms, limit=top_k,
                               max_per_video=conf.TEMPORAL_MAX_PER_VIDEO)
        timings["temporal_link"] = round((time.monotonic() - started) * 1000, 1)

        started = time.monotonic()
        results = self._enrich_chains(chains, stage_fused)
        timings["enrich"] = round((time.monotonic() - started) * 1000, 1)

        return SearchResult(
            results=results, timings=timings, signals_used=signals,
            temporal={
                "stages": [_stage_label(stage) for stage in parsed.stages],
                "deltas_ms": parsed.gaps_ms,
                "stage_counts": [len(hits) for hits in stage_rows],
                "chains": len(results),
            })

    def _link_rows(self, fused) -> list:
        """
        The cheap projection of a stage's hits that temporal.link works on. See LINK_ROWS.

        `score` is the full-precision fused score. It used to reach linking via the enriched dict,
        where it had already been rounded to 6 places for display, so chain scores were sums of
        rounded numbers -- off by ~1e-6 per chain, and in principle able to reorder two neighbours.
        """
        if not fused:
            return []
        with self.conn.cursor() as cur:
            cur.execute(LINK_ROWS, {"keyframe_ids": [item.keyframe_id for item in fused]})
            meta = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

        rows = []
        for item in fused:
            found = meta.get(item.keyframe_id)
            if found is None:  # a keyframe deleted between the retriever and here
                continue
            video_id, ts_ms = found
            rows.append({"keyframe_id": item.keyframe_id, "scene_id": item.scene_id,
                         "video_id": video_id, "ts_ms": ts_ms, "score": item.score})
        return rows

    def _enrich_chains(self, chains, stage_fused) -> list:
        """
        Full detail for the hits that made it into a chain, in one round trip for the whole page.

        Every (chain, stage) position is enriched separately even when two chains share a keyframe:
        each result carries its own score and signals, and the rows behind them are read-only.
        """
        picks, positions = [], []
        for chain_index, chain in enumerate(chains):
            for stage_index, row in enumerate(chain.scenes):
                item = stage_fused[stage_index].get(row["keyframe_id"])
                if item is None:
                    continue
                picks.append(item)
                positions.append((chain_index, stage_index))

        enriched = {}
        for pick_index, item in self._enrich_rows(picks):
            enriched[positions[pick_index]] = item

        results = []
        for chain_index, chain in enumerate(chains):
            scenes = [enriched.get((chain_index, stage_index))
                      for stage_index in range(len(chain.scenes))]
            if any(scene is None for scene in scenes):  # a partial chain is not a result
                continue
            anchor = scenes[0]
            anchor["score"] = round(chain.score, 6)
            anchor["temporal_partners"] = scenes[1:]
            anchor["temporal_gaps_ms"] = chain.gaps_ms
            results.append(anchor)
        return results

    @_ends_transaction
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

        fused = [fusion.FusedResult(keyframe_id=kf, scene_id=scene_id, score=1.0 / (rank + 1),
                                    signals={"similar": rank + 1},
                                    contributions={"similar": 1.0 / (rank + 1)})
                 for rank, (scene_id, kf) in enumerate(pairs)
                 if kf is not None and kf != keyframe_id][:top_k]
        return SearchResult(results=self._enrich(fused), timings=timings,
                            signals_used=["similar"])

    def _enrich(self, fused):
        return [item for _, item in self._enrich_rows(fused)]

    def _enrich_rows(self, fused):
        """
        As _enrich, but each result is paired with the index of the input it came from.

        A hit whose keyframe has gone missing is dropped, so the output is not positional -- and
        _enrich_chains has to put results back against the chain slot that asked for them, which
        the index is what makes possible.
        """
        if not fused:
            return []
        keyframe_ids = [f.keyframe_id for f in fused]

        with self.conn.cursor() as cur:
            cur.execute(ENRICH, {"keyframe_ids": keyframe_ids})
            rows = {r[0]: r for r in cur.fetchall()}

        out = []
        for index, item in enumerate(fused):
            row = rows.get(item.keyframe_id)
            if row is None:
                continue
            (keyframe_id, kf_index, frame_number, ts_ms,
             scene_id, video_id, shot_index, start_frame, end_frame, start_ms, end_ms,
             fps, duration_ms, damaged, keyframes) = row
            out.append((index, {
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
                "keyframes": keyframes or [],
                "score": round(item.score, 6),
                "signals": item.signals,
                "contributions": item.contributions,
            }))
        return out


def _stage_label(stage) -> str:
    """
    What a stage actually searched for, for the UI header.

    Rebuilt from the parsed parts rather than echoed from the raw segment, so an exclusion written
    mid-sequence does not show up as part of the thing being searched for.
    """
    parts = []
    if stage.ocr_phrase:
        parts.append(f'text:"{stage.ocr_phrase}"')
    if stage.asr_phrase:
        parts.append(f'said:"{stage.asr_phrase}"')
    if stage.has_free_text:
        parts.append(stage.free_text)
    return " ".join(parts)


def normalize_collection(collection):
    """Collections are stored uppercase (V3C1, MVK, GYNSURG). None means every collection."""
    return collection.strip().upper() if collection else None


def _to_pgvector(values) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"

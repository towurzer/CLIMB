"""
Search: parse the query, run the retrievers, fuse, enrich.

Replaces search_engine.py, which searched a `shots` table that no longer exists, ordered by an expression pgvector could not index, and sorted NULLs first.
"""

import dataclasses
import functools
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import custom_logger
from config import Config
from db.pool import ConnectionPool
from pipeline import device, visual_models
from pipeline.embed import vector_counts
from retrieval import fusion, query_parser, retrievers, temporal, widen
from retrieval.retrievers import Scope

SELECT_MODEL = "SELECT model_id, name, dims FROM embedding_model WHERE name = %s;"

KEYFRAMES_PER_COLLECTION = """
    SELECT v.collection, count(*)
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    GROUP BY v.collection;
"""

# The sources a caller can switch off
SOURCE_RETRIEVERS = {
    "visual": ("visual",),
    "ocr": ("ocr",),
    "asr": ("transcript",),
    "caption": ("caption",),
    "info": ("info", "tags"),
    # A collection's own visual model (PeskaVLP for LHE), with its own tickbox for that dataset.
    **{signal: (signal,) for signal in sorted(set(visual_models.SIGNALS.values()))},
}

# Signals that rank videos, not keyframes. They join after the frame-level fusion
# (fusion.apply_video_prior), re-ordering what the others found.
VIDEO_SIGNALS = ("info", "tags")


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
    Ends the transaction loading the models opened on the engine's own connection.

    psycopg2 opens a transaction on the first statement, so without this the worker sits `idle in
    transaction` forever. (found a 7h39m-old transaction from a single search xd)

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


def _own_connection(method):
    """
    Hands a request a connection nobody else holds, for as long as it runs; the pool rolls it back.

    Requests used to share one, so the first to finish rolled back the others' transactions and,
    with them, the SET LOCAL settings their queries depend on, without an error: beside two other
    clients, 76 of 300 caption-only searches lost their ANN candidates (EXPERIMENTS.md, 2026-11-05).
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.connections.connection() as conn:
            return method(self, conn, *args, **kwargs)

    return wrapper


def _foreground(method):
    """An operator is waiting for this request, so the background search waits for it."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._foreground_requests.running():
            return method(self, *args, **kwargs)

    return wrapper


# One round trip for everything the UI needs about the fused keyframes.
#
# Keyed on keyframe, not scene: results are one row per keyframe now, so the row *is* the frame
# that matched. `strip` still carries the whole scene, because the panel under the player lets the
# operator scrub the rest of the shot without going back to browse.
ENRICH = """
    SELECT k.keyframe_id, k.kf_index, k.frame_number, k.ts_ms,
           s.scene_id, s.video_id, s.shot_index, s.start_frame, s.end_frame, s.start_ms, s.end_ms,
           v.fps, v.duration_ms, v.damaged, v.recording_id, t.offset_ms,
           strip.keyframes
    FROM keyframes k
             JOIN scenes s ON s.scene_id = k.scene_id
             JOIN videos v ON v.video_id = s.video_id
             JOIN video_timeline t ON t.video_id = v.video_id
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
#
# Linked per recording, on the recording's clock: an MVK dive is one recording cut into short
# clips, and `A >> B` should not stop at a clip boundary. For everything else the recording is the
# video and the offset is 0.
LINK_ROWS = """
    SELECT k.keyframe_id, t.recording_id, k.ts_ms + t.offset_ms
    FROM keyframes k
             JOIN video_timeline t ON t.video_id = k.video_id
    WHERE k.keyframe_id = ANY (%(keyframe_ids)s);
"""

VIDEO_OF = "SELECT keyframe_id, video_id FROM keyframes WHERE keyframe_id = ANY (%(ids)s);"

# LINK_ROWS and VIDEO_OF in one, once for all the rungs of a background search.
WHERE_KEYFRAMES = """
    SELECT k.keyframe_id, k.video_id, t.recording_id, k.ts_ms + t.offset_ms
    FROM keyframes k
             JOIN video_timeline t ON t.video_id = k.video_id
    WHERE k.keyframe_id = ANY (%(keyframe_ids)s);
"""

# Searches whose lists are kept for their next rung, one per client.
WIDEN_LADDERS_KEPT = 4

# `video:` and `-video:` -- and the exclusion list the x on a result card builds -- work on whole
# recordings, whether given a recording id or any one of its clips: excluding the clip you are
# looking at has to take the other seven clips of that dive with it, or the page fills straight
# back up with them. Retrievers only know clips, so this expands to every clip of the recording.
# Outside MVK the recording is the video, so this changes nothing there.
RESOLVE_VIDEOS = """
    SELECT video_id
    FROM videos
    WHERE recording_id IN (SELECT recording_id
                           FROM videos
                           WHERE video_id = ANY (%(ids)s) OR recording_id = ANY (%(ids)s));
"""

# A query that only narrows (`video:00191`, `tag:bmx`) and has nothing to rank by: the scenes of
# what is left, in order, one keyframe each. Only ever run with a restricted Scope.
LIST_SCENES = """
    SELECT k.scene_id, k.keyframe_id
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    WHERE k.kf_index = 0
      AND {scope}
    ORDER BY k.video_id, k.ts_ms
    LIMIT %(limit)s;
""".format(scope=retrievers.KEYFRAME_SCOPE)


@dataclass
class SearchResult:
    results: list
    timings: dict
    signals_used: list
    # Present only for a sequence query: what it was understood to mean, and how much each stage
    # found. Without it an empty page cannot be told apart from a stage that matched nothing.
    temporal: dict = None
    # Present when the query had requirements (text:, said:, tag:, video:): each one and how many
    # videos satisfied it, for the same reason.
    filters: list = None
    # The first rung of the background search (retrieval/widen.py), for a sequence only.
    widen_next_depth: int = None


@dataclass
class WidenResult:
    """One rung of the background search: the chains it adds, and the rung after it, if any."""
    depth: int
    results: list = dataclasses.field(default_factory=list)
    next_depth: int = None
    timings: dict = dataclasses.field(default_factory=dict)
    # A newer request of the same client took over, so this one did nothing.
    cancelled: bool = False


def choose_visual_model(preferred, other, counts, keyframes):
    """
    The visual model to search with, and a warning when it is not simply the preferred one.

    `preferred` is the model for this machine's hardware, `other` the second one `embed` embeds
    with, `counts` how many keyframes have a vector from each, out of `keyframes`. A model only
    finds the keyframes it has vectors for, so one that covers all of them wins: a second pass
    stopped half way must not be what the GPU machines search with.
    """
    have = {name: counts.get(name, 0) for name in (preferred, other)}
    if have[preferred] >= keyframes:
        return preferred, None
    if other != preferred and have[other] >= keyframes:
        return other, (f"{preferred} has vectors for {have[preferred]:,} of {keyframes:,} "
                       f"keyframes, searching with {other}")
    best = max((preferred, other), key=lambda name: have[name])
    return best, (f"no visual model has vectors for all {keyframes:,} keyframes, searching with "
                  f"{best} ({have[best]:,})")


class SearchEngine:
    """
    Holds the query-side models.

    Only the SigLIP2 *text* tower is loaded. The old code loaded the full AutoModel, so the vision
    tower -- some 2 GB, and useless once the collection is embedded -- sat in RAM.
    """

    def __init__(self, conn):
        self.conf = Config()
        self.logger = custom_logger.get_logger("search")
        # Reads what the engine needs at startup. Requests never touch it: see _own_connection.
        self.conn = conn
        self.connections = ConnectionPool(self.conf.SEARCH_CONNECTIONS)
        self.device = device.pick_device()
        self.visual_model = None
        self.text_model = None
        # collection -> loaded spec of the domain model that runs next to SigLIP2 there
        # (CLIMB_COLLECTION_MODELS): PeskaVLP for LHE.
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
        # collection -> keyframes, read at startup: which collections are small enough for an
        # exact search (EXACT_COLLECTION_MAX_KEYFRAMES). A collection that grows while the worker
        # runs keeps its old count until the next start.
        self.collection_keyframes = {}
        # Extra connections for running the retrievers of one search side by side; None runs
        # them one after another on the request's connection (RETRIEVER_CONNECTIONS = 1).
        workers = self.conf.RETRIEVER_CONNECTIONS
        self.pool = ConnectionPool(workers) if workers > 1 else None
        self._retriever_threads = ThreadPoolExecutor(workers) if workers > 1 else None
        # The background search: its own connections and threads so a foreground search never
        # queues behind it, one rung at a time, and a new query of a client stops its old one.
        workers = self.conf.WIDEN_CONNECTIONS
        self._widen_lane = (ConnectionPool(workers) if workers > 1 else None,
                            ThreadPoolExecutor(workers) if workers > 1 else None)
        self._widen_lock = threading.Lock()
        self._widen_turns = widen.Turns()
        self._foreground_requests = widen.Foreground()
        self._ladders = OrderedDict()
        self._load_models()

    def _resolve(self, name):
        with self.conn.cursor() as cur:
            cur.execute(SELECT_MODEL, (name,))
            return cur.fetchone()


    def _load_visual(self, model_name):
        """Loads a visual model's text side, or None when it is not registered."""
        registered = self._resolve(model_name)
        if not registered:
            return None
        model_id, name, dims = registered
        encode = visual_models.load_text_side(name, self.device)
        self.logger.info(f"Loaded {name} text side ({dims}d) on {self.device}")
        return {"model_id": model_id, "name": name, "dims": dims, "encode": encode,
                "signal": visual_models.signal_name(name)}

    def domain_model_for(self, collection):
        """
        The model that searches a collection next to SigLIP2, or None.

        Only for a search limited to that collection: its vectors exist for that collection's
        keyframes alone, so in an all-datasets search it would add hits from one collection only.
        """
        return self.collection_models.get(collection.upper()) if collection else None

    # Loading reads the vector counts and embedding_model. Left open, that transaction holds its
    # locks until the first search, which a pending migration refuses, so `main.py migrate` waited
    # on the worker for as long as it ran.
    @_ends_transaction
    def _load_models(self):
        from transformers import AutoModel, AutoTokenizer

        counts, keyframes = vector_counts(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(KEYFRAMES_PER_COLLECTION)
            self.collection_keyframes = dict(cur.fetchall())
        preferred = self.conf.search_model(self.device)
        other = next((m for m in self.conf.embed_models() if m != preferred), preferred)
        visual, warning = choose_visual_model(preferred, other, counts, keyframes)
        if warning:
            self.logger.warning(warning)
        self.visual_model = self._load_visual(visual)

        # Domain models, one per collection that has one, searching next to SigLIP2. Only loaded
        # when the model is both configured and has vectors: a typo in the env var, or a model not
        # embedded yet, must leave the collection on SigLIP2 alone rather than add a signal that
        # finds nothing.
        for collection, model_name in self.conf.collection_models().items():
            if model_name == visual:
                continue
            spec = self._load_visual(model_name) if counts.get(model_name) else None
            if spec:
                self.collection_models[collection.upper()] = spec
                self.logger.info(f"{collection}: {model_name} searches next to {visual}")
            else:
                self.logger.warning(
                    f"{collection}: '{model_name}' is not registered in embedding_model or has "
                    f"no keyframe vectors, searching {collection} with {visual} alone")

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
        return [_to_pgvector(v) for v in spec["encode"](texts)]

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

    @_foreground
    @_own_connection
    def search(self, conn, query: str, exclude=None, top_k=None, collection=None,
               weights=None, depth=None, sources=None) -> SearchResult:
        conf = self.conf
        parsed = query_parser.parse_temporal(
            query, conf.TEMPORAL_DEFAULT_DELTA_MS, conf.TEMPORAL_MAX_DELTA_MS,
            conf.TEMPORAL_MAX_STAGES)
        top_k = top_k or conf.SEARCH_TOP_K
        domain = self.domain_model_for(normalize_collection(collection))
        weights = {**conf.rrf_weights(),
                   **({domain["signal"]: conf.RRF_WEIGHT_DOMAIN} if domain else {}),
                   **(weights or {})}
        scope, filters = self._base_scope(conn, parsed, exclude, normalize_collection(collection))

        allowed = _allowed_retrievers(sources)

        if parsed.is_temporal:
            return self._search_temporal(conn, parsed, scope, filters, weights, top_k, depth,
                                         allowed)

        fused, timings, lists, stage_filters = self._retrieve(
            conn, parsed.stages[0], scope, weights, depth or conf.RETRIEVER_DEPTH, top_k, allowed)
        filters += stage_filters
        return SearchResult(results=self._enrich(conn, fused), timings=timings,
                            signals_used=[n for n, r in lists.items() if r],
                            filters=filters or None)

    def _resolve_videos(self, conn, ids) -> tuple:
        """Every clip of the recordings named by clip or recording ids. Unknown ids add nothing."""
        if not ids:
            return ()
        with conn.cursor() as cur:
            cur.execute(RESOLVE_VIDEOS, {"ids": list(ids)})
            return tuple(sorted(row[0] for row in cur.fetchall()))

    def _base_scope(self, conn, parsed, exclude, collection) -> tuple:
        """
        The Scope every stage starts from: the request's and the query's exclusions, the
        collection, and `video:`. Returns (scope, filters) with `video:` reported as a filter.
        """
        excluded = self._resolve_videos(
            conn, list(dict.fromkeys((exclude or []) + parsed.exclude_videos)))
        scope = Scope(exclude=excluded, collection=collection,
                      small_collection=self._small_collection(collection))
        if not parsed.only_videos:
            return scope, []
        only = tuple(v for v in self._resolve_videos(conn, parsed.only_videos)
                     if v not in set(excluded))
        label = " ".join(f"video:{v}" for v in parsed.only_videos)
        return dataclasses.replace(scope, only=only), [{"filter": label, "videos": len(only)}]

    def _small_collection(self, collection) -> bool:
        """Whether a search limited to `collection` ranks by exact distance over all of it."""
        return (collection is not None
                and self.collection_keyframes.get(collection, 0)
                <= self.conf.EXACT_COLLECTION_MAX_KEYFRAMES)

    def _apply_filters(self, conn, stage, scope) -> tuple:
        """
        Narrows a Scope to the videos where every text:, said: and tag: of the stage holds.

        Each requirement is evaluated inside what the previous ones left over, so the reported
        counts read as a funnel and an empty one names the requirement that emptied it. Returns
        (scope, filters); scope.only == () once nothing is left.
        """
        requirements = ([(f'text:"{p}"', retrievers.videos_with_ocr_phrase, p)
                         for p in stage.ocr_phrases]
                        + [(f'said:"{p}"', retrievers.videos_with_speech, p)
                           for p in stage.asr_phrases]
                        + [(f'tag:"{t}"', retrievers.videos_with_tag, t) for t in stage.tags])
        filters = []
        for label, find, value in requirements:
            if scope.only == ():
                filters.append({"filter": label, "videos": 0})
                continue
            videos = tuple(sorted(find(conn, value, scope)))
            scope = dataclasses.replace(scope, only=videos)
            filters.append({"filter": label, "videos": len(videos)})
        return scope, filters

    def _retrieve(self, conn, parsed, scope, weights, depth, limit, allowed=None, vectors=None):
        """
        Run every retriever for one query and fuse them. Returns (fused, timings, lists, filters).

        The stage's requirements are applied first (see _apply_filters), so every retriever only
        ranks inside the videos that satisfy all of them.

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

        started = time.monotonic()
        scope, filters = self._apply_filters(conn, parsed, scope)
        if filters:
            timings["filters"] = round((time.monotonic() - started) * 1000, 1)
        if scope.only == ():
            return [], timings, lists, filters

        jobs, weights = self._retriever_jobs(parsed, scope, weights, depth, allowed, vectors, timings)
        for name, ranked, ms in self._run_retrievers(conn, jobs):
            lists[name] = ranked
            timings[name] = ms
        self._list_when_nothing_ranks(conn, parsed, scope, lists, limit, timings)

        fused = self._fused(lists, weights, limit, lambda hits: self._video_of(conn, hits), timings)
        return fused, timings, lists, filters

    def _retriever_jobs(self, parsed, scope, weights, depth, allowed, vectors, timings,
                        oversample=None, video_scope=None) -> tuple:
        """
        The retrievers one query runs, as (name, fn(conn)), and the weights to fuse them with.

        `oversample` overrides how far the vector retrievers look. `video_scope` is what the
        video-level signals rank in when that is wider than `scope`.
        """
        collection = scope.collection
        video_scope = video_scope or scope

        def enabled(name):
            return allowed is None or name in allowed

        jobs = []

        def run(name, fn):
            """Queues a retriever, `fn(conn)`; the queued ones all run together further down."""
            jobs.append((name, fn))

        def embed(kind, fn):
            """Embedding is the most expensive part of a search; it belongs in the timings."""
            if kind in vectors:
                return vectors[kind]
            started = time.monotonic()
            vector = fn()
            timings["embed"] = round(timings.get("embed", 0.0)
                                     + (time.monotonic() - started) * 1000, 1)
            return vector

        # Order below carries no meaning: fusion sums per keyframe and breaks ties on keyframe_id,
        # so which retriever runs first changes neither the ranking nor which frame is shown. It
        # used to, back when results collapsed to one row per scene and the first retriever to
        # reach a scene decided its frame. They do not collapse any more -- the scene-level signals
        # expand across the shot instead, see retrievers._expand_to_keyframes().
        visual_spec, domain = self.visual_model, self.domain_model_for(collection)
        if parsed.has_free_text and visual_spec:
            if enabled("visual"):
                vector = embed("visual",
                               lambda: self.embed_query_visual(parsed.free_text, visual_spec))
                run("visual", lambda conn: retrievers.visual(
                    conn, vector, visual_spec["model_id"], visual_spec["dims"],
                    scope, depth, oversample))
            # The collection's own model, searching the same keyframes in its own space; its
            # ranking fuses with SigLIP2's like any other signal. Its tickbox is its signal name.
            if domain and enabled(domain["signal"]):
                domain_vector = embed(domain["signal"],
                                      lambda: self.embed_query_visual(parsed.free_text, domain))
                run(domain["signal"], lambda conn: retrievers.visual(
                    conn, domain_vector, domain["model_id"], domain["dims"], scope, depth,
                    oversample))

            tokens, named = query_parser.ocr_terms(parsed.free_text)
            if tokens and enabled("ocr"):
                run("ocr", lambda conn: retrievers.ocr_lexical(conn, tokens, scope, depth))
                if not named:
                    # No capitalised word: every content word is ORed against the OCR text, which
                    # earns less trust than a name (RRF_WEIGHT_OCR_LOWERCASE).
                    weights = {**weights, "ocr": weights.get("ocr_lowercase", weights["ocr"])}

            # One embedding serves all three, so it is only worth computing if any is wanted.
            if self.text_model and (enabled("caption") or enabled("transcript")
                                    or enabled("info")):
                text_vector = embed("text", lambda: self.embed_query_text(parsed.free_text))
                if enabled("caption"):
                    run("caption", lambda conn: retrievers.caption(
                        conn, text_vector, self.text_model["model_id"],
                        self.text_model["dims"], scope, depth, oversample))
                if enabled("transcript"):
                    run("transcript", lambda conn: retrievers.transcript(
                        conn, text_vector, self.text_model["model_id"],
                        self.text_model["dims"], scope, depth, oversample))
                if enabled("info"):
                    run("info", lambda conn: retrievers.info(
                        conn, text_vector, self.text_model["model_id"],
                        self.text_model["dims"], video_scope, self.conf.INFO_DEPTH))

        # No embedding and no visual model needed: the query's own words against the tags.
        if parsed.has_free_text and enabled("tags"):
            run("tags", lambda conn: retrievers.tags(conn, query_parser.tag_terms(parsed.free_text),
                                                video_scope, self.conf.INFO_DEPTH))

        # Several phrases of one kind share one list, interleaved, so each keeps the phrase
        # weight and the UI's signal names stay what they were.
        if parsed.ocr_phrases:
            run("ocr_phrase", lambda conn: _interleave(
                [retrievers.ocr_phrase(conn, p, scope, depth) for p in parsed.ocr_phrases],
                depth))
        if parsed.asr_phrases:
            run("asr_phrase", lambda conn: _interleave(
                [retrievers.transcript_phrase(conn, p, scope, depth)
                 for p in parsed.asr_phrases], depth))
        return jobs, weights

    def _list_when_nothing_ranks(self, conn, parsed, scope, lists, limit, timings):
        if not any(lists.values()) and scope.only is not None and not parsed.has_free_text:
            # Nothing to rank by, only requirements (`video:00191`, `tag:bmx`): list what is left,
            # up to as many results as were asked for. The listing is the whole answer, so the
            # retriever depth (200) would cut a long video off: 00193 has 840 scenes.
            _, lists["listing"], timings["listing"] = self._timed_retriever(
                "listing", lambda c: self._list_scenes(c, scope, limit), conn)

    def _fused(self, lists, weights, limit, video_of, timings, only=None) -> list:
        """
        Fuses one query's lists, best first. `video_of` maps fused hits to {keyframe: video}.
        `only` restricts the output to these keyframes.

        The video-level signals hold video ids, not keyframes, so they join after the frame-level
        fusion rather than in it, all of them before the cut to `limit`.
        """
        frame_lists = {name: ranked for name, ranked in lists.items() if name not in VIDEO_SIGNALS}
        video_lists = {name: videos for name in VIDEO_SIGNALS if (videos := lists.get(name))}
        fused = fusion.fuse(frame_lists, weights, limit=None if video_lists else limit, only=only)
        if video_lists:
            videos_of_hits = video_of(fused)
            for name, videos in video_lists.items():
                started = time.monotonic()
                fused = fusion.apply_video_prior(fused, videos_of_hits, videos,
                                                 weights.get(name, 0.0), name=name)
                timings[name] = round(timings.get(name, 0.0)
                                      + (time.monotonic() - started) * 1000, 1)
            fused = fused[:limit]
        return fused

    def _timed_retriever(self, name, fn, conn):
        """(name, ranked list, ms) of one retriever on `conn`; a failing one yields []."""
        started = time.monotonic()
        try:
            ranked = fn(conn)
        except Exception as e:
            self.logger.error(f"retriever '{name}' failed: {e}")
            ranked = []
        return name, ranked, round((time.monotonic() - started) * 1000, 1)

    def _run_retrievers(self, conn, jobs, lane=None, before_each=None) -> list:
        """
        Runs the queued (name, fn) retrievers and returns (name, ranked list, ms) in queue order:
        side by side on pooled connections when there is a pool and more than one of them, else one
        after another on the request's `conn`. Fusion sums per keyframe and breaks ties on
        keyframe_id, so running them at once changes nothing about the result. `lane` is the
        (pool, threads) to use instead of the foreground's, `before_each` runs before each retriever.
        """
        pool, threads = lane or (self.pool, self._retriever_threads)
        before_each = before_each or (lambda: None)

        def in_turn(name, fn):
            before_each()
            return self._timed_retriever(name, fn, conn)

        if pool is None or len(jobs) < 2:
            return [in_turn(name, fn) for name, fn in jobs]

        def pooled(job):
            before_each()
            name, fn = job
            try:
                with pool.connection() as own:
                    return self._timed_retriever(name, fn, own)
            except Exception as e:  # no connection to be had
                self.logger.error(f"retriever '{name}' failed: {e}")
                return name, [], 0.0

        return list(threads.map(pooled, jobs))

    def _video_of(self, conn, fused) -> dict:
        if not fused:
            return {}
        with conn.cursor() as cur:
            cur.execute(VIDEO_OF, {"ids": [item.keyframe_id for item in fused]})
            return dict(cur.fetchall())

    def _list_scenes(self, conn, scope, limit) -> list:
        with conn.cursor() as cur:
            cur.execute(LIST_SCENES, {**scope.params, "limit": limit})
            return [(row[0], row[1]) for row in cur.fetchall()]

    def _embed_stages(self, stages, collection, allowed) -> list:
        """
        Every stage's query vectors, computed before any stage runs.

        Hoisted out of the per-stage loop for two reasons: a GPU can then put all the stages
        through the towers in one pass, and the cache underneath means a refined sequence only pays
        for the stage that actually changed. One dict of vectors per stage, in query order.
        """

        def enabled(name):
            return allowed is None or name in allowed

        visual_spec, domain = self.visual_model, self.domain_model_for(collection)
        vectors = [{} for _ in stages]
        # Mirrors the guard in _retrieve: without a visual model no free-text signal runs at all.
        wanted = [i for i, stage in enumerate(stages) if stage.has_free_text] if visual_spec else []
        if not wanted:
            return vectors

        texts = [stages[i].free_text for i in wanted]
        if enabled("visual"):
            for index, vector in zip(wanted, self.embed_query_visual_batch(texts, visual_spec)):
                vectors[index]["visual"] = vector
        if domain and enabled(domain["signal"]):
            for index, vector in zip(wanted, self.embed_query_visual_batch(texts, domain)):
                vectors[index][domain["signal"]] = vector
        if self.text_model and (enabled("caption") or enabled("transcript") or enabled("info")):
            for index, vector in zip(wanted, self.embed_query_text_batch(texts)):
                vectors[index]["text"] = vector
        return vectors

    def _search_temporal(self, conn, parsed, scope, filters, weights, top_k, depth,
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
        stage_vectors = self._embed_stages(parsed.stages, scope.collection, allowed)
        timings["embed"] = round((time.monotonic() - started) * 1000, 1)

        stage_rows, stage_fused = [], []
        for index, stage in enumerate(parsed.stages):
            fused, stage_timings, lists, stage_filters = self._retrieve(
                conn, stage, scope, weights, stage_depth, conf.TEMPORAL_STAGE_TOP_K,
                allowed, vectors=stage_vectors[index])
            filters = filters + [{**f, "stage": index} for f in stage_filters]
            for name, elapsed in stage_timings.items():
                timings[f"s{index}:{name}"] = elapsed
            signals += [f"s{index}:{name}" for name, results in lists.items() if results]
            # Fusion keys on keyframe, so a keyframe is named at most once per stage.
            stage_fused.append({item.keyframe_id: item for item in fused})
            stage_rows.append(self._link_rows(conn, fused))

        started = time.monotonic()
        chains = temporal.link(stage_rows, parsed.gaps_ms, limit=top_k,
                               max_per_video=conf.TEMPORAL_MAX_PER_VIDEO)
        timings["temporal_link"] = round((time.monotonic() - started) * 1000, 1)

        started = time.monotonic()
        results = self._enrich_chains(conn, chains, stage_fused)
        timings["enrich"] = round((time.monotonic() - started) * 1000, 1)

        ladder = widen.depth_ladder(conf.WIDEN_FIRST_DEPTHS, conf.WIDEN_DEPTH_STEP,
                                    conf.WIDEN_MAX_DEPTH)
        return SearchResult(
            results=results, timings=timings, signals_used=signals, filters=filters or None,
            widen_next_depth=ladder[0] if ladder else None,
            temporal={
                "stages": [_stage_label(stage) for stage in parsed.stages],
                "deltas_ms": parsed.gaps_ms,
                "stage_counts": [len(hits) for hits in stage_rows],
                "chains": len(results),
            })

    def _link_rows(self, conn, fused) -> list:
        """
        The cheap projection of a stage's hits that temporal.link works on. See LINK_ROWS.

        `score` is the full-precision fused score. It used to reach linking via the enriched dict,
        where it had already been rounded to 6 places for display, so chain scores were sums of
        rounded numbers -- off by ~1e-6 per chain, and in principle able to reorder two neighbours.
        """
        if not fused:
            return []
        with conn.cursor() as cur:
            cur.execute(LINK_ROWS, {"keyframe_ids": [item.keyframe_id for item in fused]})
            meta = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        return _rows_for_linking(fused, meta)

    def _enrich_chains(self, conn, chains, stage_fused) -> list:
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
        for pick_index, item in self._enrich_rows(conn, picks):
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

    def widen(self, query, depth, exclude=None, collection=None, sources=None, seen=(),
              client="") -> WidenResult:
        """
        One rung of the background search of a sequence: the chains that depth adds to `seen`, the
        (recording id, anchor keyframe) of what is shown already. See retrieval/widen.py.

        One rung runs at a time. Taking the turn before the queue, not after it, means a new query
        of this client stops its old one even while that is still waiting. Between any two of its
        steps a rung waits for the foreground to finish, then checks it is still wanted.
        """
        is_latest = self._widen_turns.take(client)

        def checkpoint():
            self._foreground_requests.wait_until_idle()
            if not is_latest():
                raise widen.Superseded

        with self._widen_lock:
            try:
                checkpoint()
                return self._widen_rung(query, depth, exclude, collection, sources, seen, client,
                                        checkpoint)
            except widen.Superseded:
                return WidenResult(depth=depth, cancelled=True)

    @_own_connection
    def _widen_rung(self, conn, query, depth, exclude, collection, sources, seen, client,
                    checkpoint) -> WidenResult:
        conf = self.conf
        collection = normalize_collection(collection)
        parsed = query_parser.parse_temporal(
            query, conf.TEMPORAL_DEFAULT_DELTA_MS, conf.TEMPORAL_MAX_DELTA_MS,
            conf.TEMPORAL_MAX_STAGES)
        depths = widen.depth_ladder(conf.WIDEN_FIRST_DEPTHS, conf.WIDEN_DEPTH_STEP,
                                    conf.WIDEN_MAX_DEPTH)
        if not parsed.is_temporal or not depths:
            return WidenResult(depth=depth)

        timings = {}
        key = (query, tuple(exclude or ()), collection, tuple(sorted(sources or ())))
        ladder = self._ladders.get(client)
        same_search = ladder is not None and ladder.key == key
        if not same_search or ladder.depth < depth:
            candidates = ladder.candidates if same_search else None
            self._ladders.pop(client, None)  # the old one's memory, before the new one needs it
            # The first rung gets a pass of its own, so its chains wait for nothing deeper. One pass
            # at the top then feeds the other rungs, each a prefix of it.
            pass_depth = depth if depth == depths[0] else max(depth, depths[-1])
            started = time.monotonic()
            ladder = self._build_ladder(conn, parsed, collection, exclude, sources, key, pass_depth,
                                        candidates, timings, checkpoint)
            timings["lists"] = round((time.monotonic() - started) * 1000, 1)
            if ladder is None:  # no candidates, or a requirement none of them meet
                return WidenResult(depth=depth, timings=timings)
            self._ladders[client] = ladder
            while len(self._ladders) > WIDEN_LADDERS_KEPT:
                self._ladders.popitem(last=False)

        following = widen.next_depth(depths, depth)
        if following is None:
            del self._ladders[client]

        started = time.monotonic()
        stage_rows, stage_lists = [], []
        for stage in ladder.stages:
            checkpoint()
            frame_lists = {name: ranked[:depth] for name, ranked in stage.lists.items()
                           if name not in VIDEO_SIGNALS}
            totals, scene_of = fusion.scores(frame_lists, stage.weights)
            for name in VIDEO_SIGNALS:
                fusion.add_video_prior(totals, ladder.video_of, stage.lists.get(name),
                                       stage.weights.get(name, 0.0))
            stage_rows.append([
                {"keyframe_id": keyframe, "scene_id": scene_of[keyframe], "video_id": where[0],
                 "ts_ms": where[1], "score": score} for keyframe, score in totals.items()
                if (where := ladder.link_meta.get(keyframe)) is not None])
            stage_lists.append({**stage.lists, **frame_lists})
        timings["fuse"] = round((time.monotonic() - started) * 1000, 1)

        checkpoint()
        started = time.monotonic()
        chains = temporal.link(stage_rows, parsed.gaps_ms, limit=None,
                               max_per_video=conf.TEMPORAL_MAX_PER_VIDEO)
        fresh = widen.unseen_chains(chains, seen, conf.TEMPORAL_MAX_PER_VIDEO)
        timings["link"] = round((time.monotonic() - started) * 1000, 1)

        checkpoint()
        started = time.monotonic()
        stage_fused = []
        for index, (stage, lists) in enumerate(zip(ladder.stages, stage_lists)):
            picks = {chain.scenes[index]["keyframe_id"] for chain in fresh}
            fused = self._fused(lists, stage.weights, None, lambda hits: ladder.video_of, {},
                                only=picks)
            stage_fused.append({item.keyframe_id: item for item in fused})
        results = self._enrich_chains(conn, fresh, stage_fused)
        timings["enrich"] = round((time.monotonic() - started) * 1000, 1)
        return WidenResult(depth=depth, results=results, next_depth=following, timings=timings)

    def _build_ladder(self, conn, parsed, collection, exclude, sources, key, depth, candidates,
                      timings, checkpoint):
        """
        Ranks each stage inside the candidate videos, `depth` deep, picking the videos unless
        `candidates` already names them. None when no chain can come of it.
        """
        conf = self.conf
        allowed = _allowed_retrievers(sources)
        domain = self.domain_model_for(collection)
        weights = {**conf.rrf_weights(),
                   **({domain["signal"]: conf.RRF_WEIGHT_DOMAIN} if domain else {})}
        scope, _ = self._base_scope(conn, parsed, exclude, collection)
        vectors = self._embed_stages(parsed.stages, collection, allowed)
        stage_scopes = [self._apply_filters(conn, stage, scope)[0] for stage in parsed.stages]
        if any(stage_scope.only == () for stage_scope in stage_scopes):
            return None

        started = time.monotonic()
        if candidates is None:
            candidates = self._candidate_videos(conn, stage_scopes, vectors, allowed, checkpoint)
        timings["candidates"] = round((time.monotonic() - started) * 1000, 1)
        if not candidates:
            return None
        excluded = set(scope.exclude)
        narrow = dataclasses.replace(
            scope, always_exact=True,
            only=tuple(v for v in self._resolve_videos(conn, candidates) if v not in excluded))

        jobs, narrowed, stage_weights = [], [], []
        for index, stage in enumerate(parsed.stages):
            inside, _ = self._apply_filters(conn, stage, narrow)
            if inside.only == ():
                return None
            stage_jobs, fuse_with = self._retriever_jobs(
                stage, inside, weights, depth, allowed, vectors[index], timings, oversample=depth,
                video_scope=stage_scopes[index])
            jobs += [((index, name), fn) for name, fn in stage_jobs]
            narrowed.append(inside)
            stage_weights.append(fuse_with)

        started = time.monotonic()
        per_stage = [{} for _ in parsed.stages]
        for (index, name), ranked, _ in self._run_retrievers(conn, jobs, lane=self._widen_lane,
                                                             before_each=checkpoint):
            per_stage[index][name] = ranked
        timings["ranking"] = round((time.monotonic() - started) * 1000, 1)
        for lists, stage, inside in zip(per_stage, parsed.stages, narrowed):
            self._list_when_nothing_ranks(conn, stage, inside, lists, depth, timings)

        keyframes = {keyframe for lists in per_stage for name, ranked in lists.items()
                     if name not in VIDEO_SIGNALS for _, keyframe in ranked}
        keyframes.discard(None)
        started = time.monotonic()
        with conn.cursor() as cur:
            cur.execute(WHERE_KEYFRAMES, {"keyframe_ids": list(keyframes)})
            rows = cur.fetchall()
        timings["where"] = round((time.monotonic() - started) * 1000, 1)
        return widen.Ladder(
            key=key, depth=depth, candidates=candidates,
            video_of={keyframe: video for keyframe, video, _, _ in rows},
            link_meta={keyframe: (recording, ms) for keyframe, _, recording, ms in rows},
            stages=[widen.StageLists(lists=lists, weights=fuse_with)
                    for lists, fuse_with in zip(per_stage, stage_weights)])

    def _candidate_videos(self, conn, stage_scopes, vectors, allowed, checkpoint) -> list:
        """
        The recordings the stages' visual and caption lists point at, taking one from each in turn.
        On the 48 test sequences OCR and speech lists only took slots from these two. A stage with
        requirements also offers the videos that meet them: a chain has to pass through those.
        """
        depth = self.conf.WIDEN_SEED_DEPTH
        visual, text = self.visual_model, self.text_model
        lists, jobs = [], []
        for index, stage_scope in enumerate(stage_scopes):
            found = vectors[index]
            if stage_scope.only is not None:
                lists.append(list(stage_scope.only))
            if "visual" in found:
                jobs.append((len(lists), lambda c, v=found["visual"], s=stage_scope:
                             retrievers.visual(c, v, visual["model_id"], visual["dims"], s, depth)))
                lists.append([])
            if "text" in found and (allowed is None or "caption" in allowed):
                jobs.append((len(lists), lambda c, v=found["text"], s=stage_scope:
                             retrievers.caption(c, v, text["model_id"], text["dims"], s, depth)))
                lists.append([])

        frames = {position: [keyframe for _, keyframe in ranked]
                  for position, ranked, _ in self._run_retrievers(
                      conn, jobs, lane=self._widen_lane, before_each=checkpoint)}
        keyframes = list({keyframe for ranked in frames.values() for keyframe in ranked})
        if keyframes:
            with conn.cursor() as cur:
                cur.execute(LINK_ROWS, {"keyframe_ids": keyframes})
                recording_of = {row[0]: row[1] for row in cur.fetchall()}
            for position, ranked in frames.items():
                lists[position] = list(dict.fromkeys(recording_of[keyframe] for keyframe in ranked
                                                     if keyframe in recording_of))
        return widen.round_robin(lists, self.conf.WIDEN_VIDEOS)

    @_foreground
    @_own_connection
    def similar(self, conn, keyframe_id, exclude=None, top_k=None, collection=None) -> SearchResult:
        """Scenes that look like the given keyframe. Its own scene is dropped from the results."""
        top_k = top_k or self.conf.SEARCH_TOP_K
        collection = normalize_collection(collection)
        started = time.monotonic()
        spec = self.visual_model
        scope = Scope(exclude=self._resolve_videos(conn, exclude or []),
                      collection=collection or None,
                      small_collection=self._small_collection(collection or None))
        pairs = retrievers.similar_to_keyframe(
            conn, keyframe_id, spec["model_id"], spec["dims"], scope, top_k)
        timings = {"similar": round((time.monotonic() - started) * 1000, 1)}

        fused = [fusion.FusedResult(keyframe_id=kf, scene_id=scene_id, score=1.0 / (rank + 1),
                                    signals={"similar": rank + 1},
                                    contributions={"similar": 1.0 / (rank + 1)})
                 for rank, (scene_id, kf) in enumerate(pairs)
                 if kf is not None and kf != keyframe_id][:top_k]
        return SearchResult(results=self._enrich(conn, fused), timings=timings,
                            signals_used=["similar"])

    def _enrich(self, conn, fused):
        return [item for _, item in self._enrich_rows(conn, fused)]

    def _enrich_rows(self, conn, fused):
        """
        As _enrich, but each result is paired with the index of the input it came from.

        A hit whose keyframe has gone missing is dropped, so the output is not positional -- and
        _enrich_chains has to put results back against the chain slot that asked for them, which
        the index is what makes possible.
        """
        if not fused:
            return []
        keyframe_ids = [f.keyframe_id for f in fused]

        with conn.cursor() as cur:
            cur.execute(ENRICH, {"keyframe_ids": keyframe_ids})
            rows = {r[0]: r for r in cur.fetchall()}

        out = []
        for index, item in enumerate(fused):
            row = rows.get(item.keyframe_id)
            if row is None:
                continue
            (keyframe_id, kf_index, frame_number, ts_ms,
             scene_id, video_id, shot_index, start_frame, end_frame, start_ms, end_ms,
             fps, duration_ms, damaged, recording_id, recording_offset_ms, keyframes) = row
            out.append((index, {
                "scene_id": scene_id,
                "keyframe_id": keyframe_id,
                "video_id": video_id,
                # The MVK dive this clip belongs to, and where in it the clip starts; the video
                # itself and 0 everywhere else.
                "recording_id": recording_id,
                "recording_offset_ms": recording_offset_ms,
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


def _rows_for_linking(fused, meta) -> list:
    """What temporal.link needs of each hit. `meta` is keyframe -> (recording id, ms)."""
    rows = []
    for item in fused:
        found = meta.get(item.keyframe_id)
        if found is None:  # a keyframe deleted between the retriever and here
            continue
        video_id, ts_ms = found
        rows.append({"keyframe_id": item.keyframe_id, "scene_id": item.scene_id,
                     "video_id": video_id, "ts_ms": ts_ms, "score": item.score})
    return rows


def _stage_label(stage) -> str:
    """
    What a stage actually searched for, for the UI header.

    Rebuilt from the parsed parts rather than echoed from the raw segment, so an exclusion written
    mid-sequence does not show up as part of the thing being searched for.
    """
    parts = [f'text:"{p}"' for p in stage.ocr_phrases]
    parts += [f'said:"{p}"' for p in stage.asr_phrases]
    parts += [f'tag:"{t}"' for t in stage.tags]
    if stage.has_free_text:
        parts.append(stage.free_text)
    return " ".join(parts)


def _interleave(ranked_lists, limit) -> list:
    """Round-robin over several ranked lists, first occurrence wins, cut at `limit`."""
    seen, out = set(), []
    for rank in range(max((len(r) for r in ranked_lists), default=0)):
        for ranked in ranked_lists:
            if rank < len(ranked) and ranked[rank] not in seen:
                seen.add(ranked[rank])
                out.append(ranked[rank])
    return out[:limit]


def normalize_collection(collection):
    """Collections are stored uppercase (V3C, MVK, LHE). None means every collection."""
    return collection.strip().upper() if collection else None


def _to_pgvector(values) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"

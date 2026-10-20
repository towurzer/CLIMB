"""
The four retrievers. Each returns [(scene_id, keyframe_id)] in rank order.

Everything is keyframe-level. Results used to collapse to one row per scene, so a page of 48 was
48 distinct shots; now it is 48 keyframes, and a shot whose frames fill the top of the page is
telling you it is the answer. The scene-level retrievers (transcript, which matches a time range
rather than a frame) expand across their scene's keyframes so they can fuse on the same key --
see _expand_to_keyframes().
"""

from collections import defaultdict
from dataclasses import dataclass

from config import Config


@dataclass(frozen=True)
class Scope:
    """
    Which videos a search may answer from. Travels as one value so that no retriever can apply
    the exclusions and forget the restriction, or the other way round.

    `only` is None for "no restriction" and a tuple of clip ids otherwise; MVK recordings are
    already resolved to their clips by the engine. An empty tuple means nothing may answer.
    """
    exclude: tuple = ()
    collection: str | None = None
    only: tuple | None = None

    @property
    def params(self) -> dict:
        return {"exclude": list(self.exclude), "collection": self.collection,
                "only": None if self.only is None else list(self.only)}

    @property
    def exact(self) -> bool:
        """
        Small enough to rank by exact distance over just these videos. The ANN index answers for
        the whole collection and is filtered afterwards, so `video:00191 a red car` would keep
        whichever of the global top 1000 happen to be in 00191 -- usually none.
        """
        return self.only is not None and len(self.only) <= Config.EXACT_SCOPE_MAX_VIDEOS


# The filter every retriever applies, on whichever table carries the video id.
SCOPE_SQL = """NOT ({video} = ANY (%(exclude)s::text[]))
      AND (%(only)s::text[] IS NULL OR {video} = ANY (%(only)s::text[]))
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)"""
KEYFRAME_SCOPE = SCOPE_SQL.format(video="k.video_id")
SEGMENT_SCOPE = SCOPE_SQL.format(video="t.video_id")

# Oversample on the binary index, then rerank the candidates against the full halfvec.
#
# Two statements, not one, and deliberately so.
#
# The query vector must be a bind parameter: handed over as a joined CTE it becomes a column
# reference, pgvector cannot use the HNSW index, and it silently falls back to a sequential scan
# (57 ms against 21 ms even on a small table).
#
# Splitting the ANN from the metadata lookup removes a planner decision that gets worse with size.
# As one statement, joining the 1000 candidates back to keyframes was planned as a hash join at
# 60k rows and a merge join at 600k -- the latter walking 59,760 index entries to find its 1000
# matches. With embeddings scattered across all 10.8M keyframes rather than sitting in a
# contiguous id range, that merge join would traverse essentially the whole index. As two
# statements the ANN is a clean index scan and the lookup is a bounded `= ANY(ids)` on the primary
# key, whatever the planner is feeling.
ANN_SQL = """
    SELECT {key}
    FROM {table}
    WHERE model_id = %(model_id)s
    ORDER BY binary_quantize(embedding::halfvec(%(dims)s))::bit(%(dims)s)
          <~> binary_quantize(%(query)s::halfvec(%(dims)s))::bit(%(dims)s)
    LIMIT %(oversample)s;
"""

# The trailing key is a tiebreak. Distances tie constantly, a top-2000 caption set had 888
# distinct distances, and without it a parallel plan returns equals in whatever order it happened
# to produce, so two identical searches came back in different orders.
RERANK_SQL = """
    SELECT {key}
    FROM {table}
    WHERE {key} = ANY (%(ids)s)
      AND model_id = %(model_id)s
    ORDER BY embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s), {key};
"""

# Exact distance over the embeddings of a handful of videos, for a Scope small enough to be exact.
# Reaches the rows through the video id index on keyframes / transcript_segment.
EXACT_SQL = """
    SELECT e.{key}
    FROM {table} e
             JOIN {owner} o ON o.{key} = e.{key}
    WHERE o.video_id = ANY (%(only)s::text[])
      AND e.model_id = %(model_id)s
    ORDER BY e.embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s), e.{key}
    LIMIT %(limit)s;
"""

VISUAL_ANN = ANN_SQL.format(table="keyframe_embedding", key="keyframe_id")
VISUAL_RERANK = RERANK_SQL.format(table="keyframe_embedding", key="keyframe_id")
CAPTION_ANN = ANN_SQL.format(table="caption_embedding", key="keyframe_id")
CAPTION_RERANK = RERANK_SQL.format(table="caption_embedding", key="keyframe_id")
ASR_ANN = ANN_SQL.format(table="transcript_embedding", key="segment_id")
ASR_RERANK = RERANK_SQL.format(table="transcript_embedding", key="segment_id")
VISUAL_EXACT = EXACT_SQL.format(table="keyframe_embedding", key="keyframe_id", owner="keyframes")
CAPTION_EXACT = EXACT_SQL.format(table="caption_embedding", key="keyframe_id", owner="keyframes")
ASR_EXACT = EXACT_SQL.format(table="transcript_embedding", key="segment_id",
                             owner="transcript_segment")

# Metadata and filtering for an already-ranked set of keyframes. Single table plus a tiny join to
# videos, both on primary keys.
KEYFRAME_META = """
    SELECT k.keyframe_id, k.scene_id
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    WHERE k.keyframe_id = ANY (%(ids)s)
      AND {scope};
""".format(scope=KEYFRAME_SCOPE)

# ts_rank_cd with normalisation 32 maps to (0,1), so the floor below means something stable.
# The floor matters because this retriever is weighted far above the others: without it, OCR noise
# that happens to spell a real word would be promoted straight to the top of the page.
OCR_SEARCH = """
    SELECT k.scene_id, t.keyframe_id, ts_rank_cd(t.tsv, q.query, 32) AS rank
    FROM to_tsquery('simple', %(tsquery)s) AS q(query),
         keyframe_text t
             JOIN keyframes k ON k.keyframe_id = t.keyframe_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE t.tsv @@ q.query
      AND ts_rank_cd(t.tsv, q.query, 32) >= %(min_rank)s
      AND {scope}
    ORDER BY rank DESC, t.keyframe_id
    LIMIT %(limit)s;
""".format(scope=KEYFRAME_SCOPE)

# Trigram fallback for OCR noise: 'B0ULANGERIE' scores 0.727 against the right sign, where lexeme
# matching scores nothing at all.
OCR_TRIGRAM_SEARCH = """
    SELECT k.scene_id, t.keyframe_id, similarity(t.ocr_text, %(phrase)s) AS sim
    FROM keyframe_text t
             JOIN keyframes k ON k.keyframe_id = t.keyframe_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE t.ocr_text %% %(phrase)s
      AND {scope}
    ORDER BY sim DESC, t.keyframe_id
    LIMIT %(limit)s;
""".format(scope=KEYFRAME_SCOPE)

# Scenes an already-ranked set of transcript segments lands on.
#
# Segments carry a time range, not a keyframe, so they are mapped onto scenes by overlapping
# start_ms/end_ms. This is why scenes store milliseconds rather than only frames. The filtering
# lives here rather than beside the ANN: a WHERE next to an HNSW ORDER BY is applied *after* the
# index has already chosen its rows, so an excluded video silently eats a candidate slot.
ASR_SCENES = """
    SELECT t.segment_id, s.scene_id
    FROM transcript_segment t
             JOIN videos v ON v.video_id = t.video_id
             JOIN scenes s ON s.video_id = t.video_id
                 AND s.start_ms < t.end_ms AND s.end_ms > t.start_ms
    WHERE t.segment_id = ANY (%(segment_ids)s)
      AND NOT t.hallucination
      AND {scope};
""".format(scope=SEGMENT_SCOPE)

# DISTINCT ON has to sort by scene_id first to pick one segment per scene, which is not the order
# the caller wants -- the ranking is the whole point, and RRF reads position as rank. So the pick
# happens inside and the ranking outside, and the LIMIT with it, or the cut would keep the
# lowest-numbered scenes rather than the best-matching ones.
ASR_PHRASE_SEARCH = """
    SELECT scene_id, keyframe_id
    FROM (SELECT DISTINCT ON (s.scene_id) s.scene_id, NULL::bigint AS keyframe_id,
                                          ts_rank_cd(t.tsv, q.query, 32) AS rank
          FROM to_tsquery('simple', %(tsquery)s) AS q(query),
               transcript_segment t
                   JOIN videos v ON v.video_id = t.video_id
                   JOIN scenes s ON s.video_id = t.video_id
                       AND s.start_ms < t.end_ms AND s.end_ms > t.start_ms
          WHERE t.tsv @@ q.query
            AND NOT t.hallucination
            AND {scope}
          ORDER BY s.scene_id, rank DESC, t.segment_id) best
    ORDER BY rank DESC, scene_id
    LIMIT %(limit)s;
""".format(scope=SEGMENT_SCOPE)


def _or_tsquery(tokens) -> str | None:
    """ORs the distinctive tokens. Never ANDs -- that is what matched nothing."""
    safe = [t.replace("'", "").replace("\\", "") for t in tokens if t]
    return " | ".join(safe) if safe else None


def _phrase_tsquery(phrase: str) -> str | None:
    tokens = [t.replace("'", "").replace("\\", "") for t in phrase.split() if t]
    # Adjacency, so text:"grand hotel" does not match a frame reading "grand" and one reading "hotel".
    return " <-> ".join(tokens) if tokens else None


SCENE_KEYFRAMES = """
    SELECT scene_id, keyframe_id
    FROM keyframes
    WHERE scene_id = ANY (%(scene_ids)s)
    ORDER BY scene_id, ts_ms;
"""


def _resolve_scenes(conn, ordered_ids, scope, limit):
    """
    Filters an already-ranked keyframe list and attaches scene ids, preserving rank order.

    Every ranked keyframe survives, no collapsing to one row per scene. Ten keyframes of the
    same shot in the top of the list is the retriever saying the same thing ten times, and that
    repetition is the confidence signal.
    """
    if not ordered_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(KEYFRAME_META, {"ids": ordered_ids, **scope.params})
        scene_of = dict(cur.fetchall())

    out = []
    for keyframe_id in ordered_ids:
        scene_id = scene_of.get(keyframe_id)
        if scene_id is None:  # excluded video, or filtered out by collection
            continue
        out.append((scene_id, keyframe_id))
        if len(out) >= limit:
            break
    return out


def _expand_to_keyframes(conn, scene_rows, limit):
    """
    Turns a scene-level ranked list into a keyframe-level one.

    Transcripts match a time range, not a frame, so a hit applies to the whole shot equally,
    every keyframe of it inherits the scene's rank. Fusion keys on keyframes, so without this a
    transcript hit would score nothing at all.
    """
    scene_ids = [scene_id for scene_id, _ in scene_rows]
    if not scene_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(SCENE_KEYFRAMES, {"scene_ids": scene_ids})
        by_scene = defaultdict(list)
        for scene_id, keyframe_id in cur.fetchall():
            by_scene[scene_id].append(keyframe_id)

    out = []
    for scene_id, _ in scene_rows:
        for keyframe_id in by_scene.get(scene_id, ()):
            out.append((scene_id, keyframe_id))
            if len(out) >= limit:
                return out
    return out


def _ann_rerank(conn, ann_sql, rerank_sql, query_vector, model_id, dims, oversample=None):
    """
    Oversample on the binary index, then rerank the candidates against the full halfvec.

    One implementation for all three vector tables. A second copy would drift, and the failure mode
    is a sequential scan nobody notices until the collection is large -- which is exactly what the
    caption and transcript retrievers were doing before they were routed through here.
    """
    conf = Config()
    depth = oversample or conf.ANN_OVERSAMPLE
    with conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = %s", (min(depth, conf.HNSW_EF_SEARCH_MAX),))
        if depth > conf.HNSW_EF_SEARCH_MAX:
            # ef_search cannot go above 1000, so a larger oversample is only honoured if the scan
            # is allowed to continue past it. Without this, asking for 4000 candidates silently
            # yields 1000. Relaxed order is fine: the rerank re-sorts by exact distance immediately afterwards.
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur.execute("SET LOCAL hnsw.max_scan_tuples = %s", (conf.HNSW_MAX_SCAN_TUPLES,))
        cur.execute(ann_sql, {"model_id": model_id, "dims": dims, "query": query_vector,
                              "oversample": depth})
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        cur.execute(rerank_sql, {"ids": ids, "model_id": model_id, "dims": dims,
                                 "query": query_vector})
        return [row[0] for row in cur.fetchall()]


def _rank_vectors(conn, ann_sql, rerank_sql, exact_sql, query_vector, model_id, dims, scope,
                  oversample=None):
    """Exact over the Scope's videos when it is small enough, the ANN path otherwise."""
    if not scope.exact:
        return _ann_rerank(conn, ann_sql, rerank_sql, query_vector, model_id, dims, oversample)
    with conn.cursor() as cur:
        cur.execute(exact_sql, {"only": list(scope.only), "model_id": model_id, "dims": dims,
                                "query": query_vector,
                                "limit": oversample or Config.ANN_OVERSAMPLE})
        return [row[0] for row in cur.fetchall()]


def visual(conn, query_vector, model_id, dims, scope, limit, oversample=None):
    ranked = _rank_vectors(conn, VISUAL_ANN, VISUAL_RERANK, VISUAL_EXACT, query_vector, model_id,
                           dims, scope, oversample)
    return _resolve_scenes(conn, ranked, scope, limit)


def ocr_lexical(conn, tokens, scope, limit):
    conf = Config()
    tsquery = _or_tsquery(tokens)
    if not tsquery:
        return []
    with conn.cursor() as cur:
        cur.execute(OCR_SEARCH, {"tsquery": tsquery, **scope.params, "limit": limit,
                                 "min_rank": conf.OCR_MIN_RANK})
        return [(row[0], row[1]) for row in cur.fetchall()]


def ocr_phrase(conn, phrase, scope, limit):
    """Exact phrase search, used by text:"..." -- with a trigram pass for OCR misreadings."""
    conf = Config()
    tsquery = _phrase_tsquery(phrase)
    results = []
    if tsquery:
        with conn.cursor() as cur:
            cur.execute(OCR_SEARCH, {"tsquery": tsquery, **scope.params, "limit": limit,
                                     "min_rank": 0.0})
            results = [(row[0], row[1]) for row in cur.fetchall()]
    if len(results) < limit:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s;",
                        (conf.OCR_TRIGRAM_THRESHOLD,))
            cur.execute(OCR_TRIGRAM_SEARCH, {"phrase": phrase, **scope.params, "limit": limit})
            seen = {scene for scene, _ in results}
            results += [(row[0], row[1]) for row in cur.fetchall() if row[0] not in seen]
    return results[:limit]


KEYFRAME_VECTOR = """
    SELECT embedding::text FROM keyframe_embedding
    WHERE keyframe_id = %(keyframe_id)s AND model_id = %(model_id)s;
"""


def similar_to_keyframe(conn, keyframe_id, model_id, dims, scope, limit, oversample=None):
    """
    Find-similar, using the same ANN path as a text search.

    Lives here rather than in the Node backend so there is one implementation of
    oversample-then-rerank. A second copy would silently drift -- and the failure mode is a
    sequential scan nobody notices until the collection is large.
    """
    with conn.cursor() as cur:
        cur.execute(KEYFRAME_VECTOR, {"keyframe_id": keyframe_id, "model_id": model_id})
        row = cur.fetchone()
    if not row:
        return []
    return visual(conn, row[0], model_id, dims, scope, limit + 1, oversample=oversample)


def caption(conn, query_vector, model_id, dims, scope, limit, oversample=None):
    """
    Captions describe the shot, not the frame, so a hit expands across the scene's keyframes.

    This is not cosmetic. CAPTION_SCOPE defaults to `shot`, so only kf_index 0 is ever captioned
    and only kf 0 has a caption_embedding row. Fusion keys on keyframes, so without the expansion
    kf 0 would be the only frame of any shot able to collect a caption contribution, a second
    retriever's worth of score that none of its siblings can earn. Measured before this was added,
    that pinned kf_index 0 to all 15 of the top 15 results.
    """
    ranked = _rank_vectors(conn, CAPTION_ANN, CAPTION_RERANK, CAPTION_EXACT, query_vector,
                           model_id, dims, scope, oversample)
    scenes = _resolve_scenes(conn, ranked, scope, limit)
    return _expand_to_keyframes(conn, scenes, limit)


def transcript(conn, query_vector, model_id, dims, scope, limit, oversample=None):
    """
    Scenes that were on screen while something matching the query was being said.

    Scene order follows the best-ranked segment that reached it. The previous version did
    `DISTINCT ON (scene_id)` with no ORDER BY, so which segment won and what order the scenes came
    back in were both whatever the plan produced, and RRF then read that as a ranking.
    """
    conf = Config()
    ranked = _rank_vectors(conn, ASR_ANN, ASR_RERANK, ASR_EXACT, query_vector, model_id, dims,
                           scope, oversample or conf.ASR_SEGMENT_LIMIT)
    if not ranked:
        return []

    with conn.cursor() as cur:
        cur.execute(ASR_SCENES, {"segment_ids": ranked, **scope.params})
        scenes_of = defaultdict(list)
        for segment_id, scene_id in cur.fetchall():
            scenes_of[segment_id].append(scene_id)

    seen, scenes = set(), []
    for segment_id in ranked:
        # A segment can span several scenes; they all inherit its rank, so sort for a stable order.
        for scene_id in sorted(scenes_of.get(segment_id, ())):
            if scene_id not in seen:
                seen.add(scene_id)
                scenes.append((scene_id, None))
        if len(scenes) >= limit:
            break
    return _expand_to_keyframes(conn, scenes[:limit], limit)


def transcript_phrase(conn, phrase, scope, limit):
    tsquery = _phrase_tsquery(phrase)
    if not tsquery:
        return []
    with conn.cursor() as cur:
        cur.execute(ASR_PHRASE_SEARCH, {"tsquery": tsquery, **scope.params, "limit": limit})
        scenes = [(row[0], row[1]) for row in cur.fetchall()]
    return _expand_to_keyframes(conn, scenes, limit)


# --- video info -------------------------------------------------------------------------------

# One row per video, tens of thousands at most, so an exact scan with no index (see migration 007).
INFO_SEARCH = """
    SELECT e.video_id
    FROM video_info_embedding e
             JOIN videos v ON v.video_id = e.video_id
    WHERE e.model_id = %(model_id)s
      AND {scope}
    ORDER BY e.embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s), e.video_id
    LIMIT %(limit)s;
""".format(scope=SCOPE_SQL.format(video="e.video_id"))


def info(conn, query_vector, model_id, dims, scope, limit) -> list:
    """Videos whose title, tags and description match the query, best first. Video ids, not
    keyframes: see fusion.apply_video_prior for how they reach the ranking."""
    with conn.cursor() as cur:
        cur.execute(INFO_SEARCH, {"model_id": model_id, "dims": dims, "query": query_vector,
                                  "limit": limit, **scope.params})
        return [row[0] for row in cur.fetchall()]


# --- filters: which videos satisfy a requirement ----------------------------------------------
#
# Complete sets, not ranked lists cut at some depth: `said:"hello"` may hold in thousands of
# videos, and a requirement that silently dropped the ones past rank 1000 would be a wrong answer,
# not a slow one.

# Lexeme adjacency or the trigram fallback, the same two ways ocr_phrase() finds a phrase. A
# filter stricter than the retriever would throw away exactly the misread signs (B0ULANGERIE) the
# trigram pass exists for.
OCR_PHRASE_VIDEOS = """
    SELECT DISTINCT k.video_id
    FROM keyframe_text t
             JOIN keyframes k ON k.keyframe_id = t.keyframe_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE (t.tsv @@ to_tsquery('simple', %(tsquery)s) OR t.ocr_text %% %(phrase)s)
      AND {scope};
""".format(scope=KEYFRAME_SCOPE)

SPEECH_PHRASE_VIDEOS = """
    SELECT DISTINCT t.video_id
    FROM transcript_segment t
             JOIN videos v ON v.video_id = t.video_id
    WHERE t.tsv @@ to_tsquery('simple', %(tsquery)s)
      AND NOT t.hallucination
      AND {scope};
""".format(scope=SEGMENT_SCOPE)

# Exact tag, or a stored tag with `?` where the source lost a character: `vid?o` is how the info
# files spell vidéo, so the stored tag is used as a LIKE pattern with `?` as the one-character
# wildcard. Tags are lowercased on both sides.
TAG_VIDEOS = """
    SELECT i.video_id
    FROM video_info i
             JOIN videos v ON v.video_id = i.video_id
    WHERE (i.tags @> ARRAY [%(tag)s]
        OR EXISTS (SELECT 1
                   FROM unnest(i.tags) AS stored(tag)
                   WHERE stored.tag LIKE '%%?%%'
                     AND %(tag)s LIKE replace(replace(replace(replace(stored.tag, '\\', '\\\\'),
                                                                 '%%', '\\%%'), '_', '\\_'), '?', '_')))
      AND {scope};
""".format(scope=SCOPE_SQL.format(video="i.video_id"))


def videos_with_ocr_phrase(conn, phrase, scope) -> set:
    conf = Config()
    tsquery = _phrase_tsquery(phrase)
    if not tsquery:
        return set()
    with conn.cursor() as cur:
        cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s;", (conf.OCR_TRIGRAM_THRESHOLD,))
        cur.execute(OCR_PHRASE_VIDEOS, {"tsquery": tsquery, "phrase": phrase, **scope.params})
        return {row[0] for row in cur.fetchall()}


def videos_with_speech(conn, phrase, scope) -> set:
    tsquery = _phrase_tsquery(phrase)
    if not tsquery:
        return set()
    with conn.cursor() as cur:
        cur.execute(SPEECH_PHRASE_VIDEOS, {"tsquery": tsquery, **scope.params})
        return {row[0] for row in cur.fetchall()}


def videos_with_tag(conn, tag, scope) -> set:
    with conn.cursor() as cur:
        cur.execute(TAG_VIDEOS, {"tag": tag.strip().lower(), **scope.params})
        return {row[0] for row in cur.fetchall()}

"""
The four retrievers. Each returns [(scene_id, keyframe_id | None)] in rank order.

Everything is scene-level. The old search returned keyframes, and since a scene had ~7 of them a
48-result page collapsed to roughly 22 distinct scenes -- half the grid was the same shot again.
One row per scene is the fix, and it comes for free from fusing on scene_id.
"""

from config import Config

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
VISUAL_ANN = """
    SELECT e.keyframe_id
    FROM keyframe_embedding e
    WHERE e.model_id = %(model_id)s
    ORDER BY binary_quantize(e.embedding::halfvec(%(dims)s))::bit(%(dims)s)
          <~> binary_quantize(%(query)s::halfvec(%(dims)s))::bit(%(dims)s)
    LIMIT %(oversample)s;
"""

VISUAL_RERANK = """
    SELECT keyframe_id
    FROM keyframe_embedding
    WHERE keyframe_id = ANY (%(ids)s)
      AND model_id = %(model_id)s
    ORDER BY embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s);
"""

# Metadata and filtering for an already-ranked set of keyframes. Single table plus a tiny join to
# videos, both on primary keys.
KEYFRAME_META = """
    SELECT k.keyframe_id, k.scene_id
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    WHERE k.keyframe_id = ANY (%(ids)s)
      AND NOT (k.video_id = ANY (%(exclude)s))
      AND (%(collection)s IS NULL OR v.collection = %(collection)s);
"""

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
      AND NOT (k.video_id = ANY (%(exclude)s))
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
    ORDER BY rank DESC
    LIMIT %(limit)s;
"""

# Trigram fallback for OCR noise: 'B0ULANGERIE' scores 0.727 against the right sign, where lexeme
# matching scores nothing at all.
OCR_TRIGRAM_SEARCH = """
    SELECT k.scene_id, t.keyframe_id, similarity(t.ocr_text, %(phrase)s) AS sim
    FROM keyframe_text t
             JOIN keyframes k ON k.keyframe_id = t.keyframe_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE t.ocr_text %% %(phrase)s
      AND NOT (k.video_id = ANY (%(exclude)s))
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
    ORDER BY sim DESC
    LIMIT %(limit)s;
"""

CAPTION_SEARCH = """
    SELECT keyframe_id
    FROM caption_embedding
    WHERE model_id = %(model_id)s
    ORDER BY embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s)
    LIMIT %(limit)s;
"""

# Transcript segments carry a time range, not a keyframe, so they are mapped onto scenes by
# overlapping start_ms/end_ms. This is why scenes store milliseconds rather than only frames.
ASR_SEARCH = """
    WITH hits AS (SELECT t.video_id, t.start_ms, t.end_ms
                  FROM transcript_embedding e
                           JOIN transcript_segment t ON t.segment_id = e.segment_id
                           JOIN videos v ON v.video_id = t.video_id
                  WHERE e.model_id = %(model_id)s
                    AND NOT (t.video_id = ANY (%(exclude)s))
                    AND (%(collection)s IS NULL OR v.collection = %(collection)s)
                  ORDER BY e.embedding::halfvec(%(dims)s) <=> %(query)s::halfvec(%(dims)s)
                  LIMIT %(segment_limit)s)
    SELECT DISTINCT ON (s.scene_id) s.scene_id, NULL::bigint AS keyframe_id
    FROM hits h
             JOIN scenes s ON s.video_id = h.video_id
        AND s.start_ms < h.end_ms AND s.end_ms > h.start_ms
    LIMIT %(limit)s;
"""

ASR_PHRASE_SEARCH = """
    SELECT DISTINCT ON (s.scene_id) s.scene_id, NULL::bigint AS keyframe_id,
                                    ts_rank_cd(t.tsv, q.query, 32) AS rank
    FROM to_tsquery('simple', %(tsquery)s) AS q(query),
         transcript_segment t
             JOIN videos v ON v.video_id = t.video_id
             JOIN scenes s ON s.video_id = t.video_id
                 AND s.start_ms < t.end_ms AND s.end_ms > t.start_ms
    WHERE t.tsv @@ q.query
      AND NOT (t.video_id = ANY (%(exclude)s))
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
    ORDER BY s.scene_id, rank DESC
    LIMIT %(limit)s;
"""


def _or_tsquery(tokens) -> str | None:
    """ORs the distinctive tokens. Never ANDs -- that is what matched nothing."""
    safe = [t.replace("'", "").replace("\\", "") for t in tokens if t]
    return " | ".join(safe) if safe else None


def _phrase_tsquery(phrase: str) -> str | None:
    tokens = [t.replace("'", "").replace("\\", "") for t in phrase.split() if t]
    # Adjacency, so text:"grand hotel" does not match a frame reading "grand" and one reading "hotel".
    return " <-> ".join(tokens) if tokens else None


def _resolve_scenes(conn, ordered_ids, exclude, collection, limit):
    """Filters an already-ranked keyframe list and attaches scene ids, preserving rank order."""
    if not ordered_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(KEYFRAME_META, {"ids": ordered_ids, "exclude": exclude,
                                    "collection": collection})
        scene_of = dict(cur.fetchall())

    seen, out = set(), []
    for keyframe_id in ordered_ids:
        scene_id = scene_of.get(keyframe_id)
        # One row per scene: the highest-ranked keyframe of a scene represents it.
        if scene_id is None or scene_id in seen:
            continue
        seen.add(scene_id)
        out.append((scene_id, keyframe_id))
        if len(out) >= limit:
            break
    return out


def visual(conn, query_vector, model_id, dims, exclude, collection, limit, oversample=None):
    conf = Config()
    depth = oversample or conf.ANN_OVERSAMPLE
    with conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = %s", (min(depth, conf.HNSW_EF_SEARCH_MAX),))
        if depth > conf.HNSW_EF_SEARCH_MAX:
            # ef_search cannot go above 1000, so a larger oversample is only honoured if the scan
            # is allowed to continue past it. Without this, asking for 4000 candidates silently
            # yields 1000. Relaxed order is fine: VISUAL_RERANK re-sorts by exact distance immediately afterwards.
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur.execute("SET LOCAL hnsw.max_scan_tuples = %s", (conf.HNSW_MAX_SCAN_TUPLES,))
        cur.execute(VISUAL_ANN, {"model_id": model_id, "dims": dims, "query": query_vector,
                                 "oversample": depth})
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        cur.execute(VISUAL_RERANK, {"ids": ids, "model_id": model_id, "dims": dims,
                                    "query": query_vector})
        ranked = [row[0] for row in cur.fetchall()]
    return _resolve_scenes(conn, ranked, exclude, collection, limit)


def ocr_lexical(conn, tokens, exclude, collection, limit):
    conf = Config()
    tsquery = _or_tsquery(tokens)
    if not tsquery:
        return []
    with conn.cursor() as cur:
        cur.execute(OCR_SEARCH, {"tsquery": tsquery, "exclude": exclude, "collection": collection,
                                 "limit": limit, "min_rank": conf.OCR_MIN_RANK})
        return [(row[0], row[1]) for row in cur.fetchall()]


def ocr_phrase(conn, phrase, exclude, collection, limit):
    """Exact phrase search, used by text:"..." -- with a trigram pass for OCR misreadings."""
    conf = Config()
    tsquery = _phrase_tsquery(phrase)
    results = []
    if tsquery:
        with conn.cursor() as cur:
            cur.execute(OCR_SEARCH, {"tsquery": tsquery, "exclude": exclude,
                                     "collection": collection, "limit": limit,
                                     "min_rank": 0.0})
            results = [(row[0], row[1]) for row in cur.fetchall()]
    if len(results) < limit:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL pg_trgm.similarity_threshold = %s;",
                        (conf.OCR_TRIGRAM_THRESHOLD,))
            cur.execute(OCR_TRIGRAM_SEARCH, {"phrase": phrase, "exclude": exclude,
                                             "collection": collection, "limit": limit})
            seen = {scene for scene, _ in results}
            results += [(row[0], row[1]) for row in cur.fetchall() if row[0] not in seen]
    return results[:limit]


KEYFRAME_VECTOR = """
    SELECT embedding::text FROM keyframe_embedding
    WHERE keyframe_id = %(keyframe_id)s AND model_id = %(model_id)s;
"""


def similar_to_keyframe(conn, keyframe_id, model_id, dims, exclude, collection, limit,
                        oversample=None):
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
    return visual(conn, row[0], model_id, dims, exclude, collection, limit + 1,
                  oversample=oversample)


def caption(conn, query_vector, model_id, dims, exclude, collection, limit):
    with conn.cursor() as cur:
        cur.execute(CAPTION_SEARCH, {"model_id": model_id, "dims": dims,
                                     "query": query_vector, "limit": limit * 4})
        ranked = [row[0] for row in cur.fetchall()]
    return _resolve_scenes(conn, ranked, exclude, collection, limit)


def transcript(conn, query_vector, model_id, dims, exclude, collection, limit):
    conf = Config()
    with conn.cursor() as cur:
        cur.execute(ASR_SEARCH, {"model_id": model_id, "dims": dims, "query": query_vector,
                                 "exclude": exclude, "collection": collection, "limit": limit,
                                 "segment_limit": conf.ASR_SEGMENT_LIMIT})
        return [(row[0], row[1]) for row in cur.fetchall()]


def transcript_phrase(conn, phrase, exclude, collection, limit):
    tsquery = _phrase_tsquery(phrase)
    if not tsquery:
        return []
    with conn.cursor() as cur:
        cur.execute(ASR_PHRASE_SEARCH, {"tsquery": tsquery, "exclude": exclude,
                                        "collection": collection, "limit": limit})
        return [(row[0], row[1]) for row in cur.fetchall()]

"""
The expensive derived indexes: the ANN vector index and the full-text GIN indexes.

Typical order of operations for a bulk load:

    climb-pipe index drop        # before a large COPY, if the indexes already exist
    ... load stages ...
    climb-pipe index build       # once, at the end
"""

import custom_logger
from config import Config

# Binary-quantized HNSW, one partial index per embedding model.
#
# Quantized rather than a plain halfvec index because of size: at 12.4M keyframes a halfvec HNSW
# is ~28 GB, dangerously close to a standard 32GB and far too much for a 16GB machine, while the
# bit(1024) version is ~5.3 GB. The oversample-then-rerank query recovers the precision.
#
# One index per model, because models differ in dimension (1024 for SigLIP2, 512 for the domain
# models earmarked for MVK and GynSurg) and occupy different vector spaces. The cast in the
# expression is what lets a single dimensionless column carry all of them.
ANN_INDEX_PREFIX = "keyframe_embedding_m"

CREATE_ANN_INDEX = """
    CREATE INDEX IF NOT EXISTS {name} ON keyframe_embedding
    USING hnsw ((binary_quantize(embedding::halfvec({dims}))::bit({dims})) bit_hamming_ops)
    WITH (m = {m}, ef_construction = {ef_construction})
    WHERE model_id = {model_id};
"""

SELECT_MODELS_WITH_ROWS = """
    SELECT m.model_id, m.name, m.dims
    FROM embedding_model m
    WHERE EXISTS (SELECT 1 FROM keyframe_embedding e WHERE e.model_id = m.model_id)
    ORDER BY m.model_id;
"""


def ann_index_name(model_id: int) -> str:
    return f"{ANN_INDEX_PREFIX}{model_id}_bq_hnsw"

TEXT_INDEXES = [
    ("keyframe_text_tsv_idx",
     "CREATE INDEX IF NOT EXISTS keyframe_text_tsv_idx ON keyframe_text USING gin (tsv);"),

    ("keyframe_text_trgm_idx",
     "CREATE INDEX IF NOT EXISTS keyframe_text_trgm_idx ON keyframe_text USING gin (ocr_text gin_trgm_ops);"),

    ("keyframe_caption_tsv_idx",
     "CREATE INDEX IF NOT EXISTS keyframe_caption_tsv_idx ON keyframe_caption USING gin (tsv);"),

    ("transcript_segment_tsv_idx",
     "CREATE INDEX IF NOT EXISTS transcript_segment_tsv_idx ON transcript_segment USING gin (tsv);"),
]

ANALYZE_TABLES = ["keyframe_embedding", "keyframe_text", "keyframe_caption", "transcript_segment"]


def apply_build_tuning(conn):
    """
    Raises the session's index-build budget.

    maintenance_work_mem is the single setting that decides whether a large HNSW build
    finishes in hours or days: below the graph's working size pgvector spills to disk and
    the build slows by an order of magnitude.
    """
    conf = Config()
    logger = custom_logger.get_logger("index_ops")

    with conn.cursor() as cur:
        cur.execute(f"SET maintenance_work_mem = '{conf.PG_BUILD_MAINTENANCE_WORK_MEM}';")
        cur.execute(f"SET max_parallel_maintenance_workers = {conf.PG_BUILD_PARALLEL_WORKERS};")
        cur.execute("SHOW maintenance_work_mem;")
        effective = cur.fetchone()[0]

    # Postgres silently clamps this to the server's limits, so report what actually took
    # effect rather than what we asked for.
    logger.info(
        f"Index build tuning: maintenance_work_mem={effective} "
        f"(requested {conf.PG_BUILD_MAINTENANCE_WORK_MEM}), "
        f"max_parallel_maintenance_workers={conf.PG_BUILD_PARALLEL_WORKERS}"
    )


def apply_serve_tuning(conn):
    """
    Applies the query-time settings that cannot be set on the container command line.

    hnsw.ef_search is the one that matters and the one that silently breaks things: pgvector
    returns at most ef_search rows from an HNSW scan, so if it is left at the default of 40
    the oversample step asks for 1000 candidates and quietly gets 40, gutting rerank quality
    with no error anywhere.
    """
    conf = Config()
    logger = custom_logger.get_logger("index_ops")

    with conn.cursor() as cur:
        cur.execute(f"SET hnsw.ef_search = {conf.PG_SERVE_HNSW_EF_SEARCH};")
        cur.execute(f"SET work_mem = '{conf.PG_SERVE_WORK_MEM}';")

    logger.debug(
        f"Serve tuning: hnsw.ef_search={conf.PG_SERVE_HNSW_EF_SEARCH}, "
        f"work_mem={conf.PG_SERVE_WORK_MEM}"
    )


def build_indexes(conn, ann=True, text=True, analyze=True):
    """Creates the derived indexes. Safe to re-run; each statement is IF NOT EXISTS."""
    conf = Config()
    logger = custom_logger.get_logger("index_ops")

    apply_build_tuning(conn)

    if ann:
        for model_id, name, dims in embedded_models(conn):
            index = ann_index_name(model_id)
            logger.info(f"Building {index} for {name} ({dims}d) -- this is the long one...")
            with conn.cursor() as cur:
                cur.execute(CREATE_ANN_INDEX.format(
                    name=index, dims=dims, model_id=model_id,
                    m=conf.HNSW_M, ef_construction=conf.HNSW_EF_CONSTRUCTION,
                ))
            conn.commit()
            logger.info(f"Built {index}.")

    if text:
        for name, sql in TEXT_INDEXES:
            logger.info(f"Building {name}...")
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        logger.info(f"Built {len(TEXT_INDEXES)} text index(es).")

    if analyze:
        # Without fresh statistics the planner will not choose the HNSW index it just built.
        for table in ANALYZE_TABLES:
            with conn.cursor() as cur:
                cur.execute(f"ANALYZE {table};")
            conn.commit()
        logger.info("Refreshed table statistics.")


def drop_indexes(conn, ann=True, text=True):
    """Drops the derived indexes so a bulk COPY does not have to maintain them."""
    logger = custom_logger.get_logger("index_ops")

    targets = []
    if ann:
        targets.extend(ann_index_name(m[0]) for m in embedded_models(conn))
    if text:
        targets.extend(name for name, _ in TEXT_INDEXES)

    for name in targets:
        with conn.cursor() as cur:
            cur.execute(f"DROP INDEX IF EXISTS {name};")
        conn.commit()
        logger.info(f"Dropped {name}.")


def embedded_models(conn):
    """Returns [(model_id, name, dims)] for models that actually have embeddings stored."""
    with conn.cursor() as cur:
        cur.execute(SELECT_MODELS_WITH_ROWS)
        return cur.fetchall()


def index_status(conn):
    """Returns [(index_name, exists, size_pretty)] for reporting."""
    expected = ([ann_index_name(m[0]) for m in embedded_models(conn)]
                + [name for name, _ in TEXT_INDEXES])

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid))
            FROM pg_stat_user_indexes
            WHERE indexrelname = ANY (%s);
            """,
            (expected,),
        )
        present = dict(cur.fetchall())

    return [(name, name in present, present.get(name, "-")) for name in expected]

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

# Every table that is searched by vector at query time.
#
# All three used to be listed here only in spirit: keyframes got an index and captions and
# transcripts got a sequential scan over the whole table on every single query. That is invisible
# at 14k rows and fatal at V3C's millions, so they are built the same way now -- and searched the
# same way too, oversample on the binary index then rerank exact (see retrieval/retrievers.py).
#
# It has to be binary_quantize() rather than a plain halfvec index, and not only for the 5x size
# saving: `embedding` is a dimensionless halfvec column so that one column can carry every model,
# and an index on the typmod cast `embedding::halfvec(768)` is one Postgres will build and then
# never match against the identical expression in a query. The binary_quantize() call is a real
# function, so the expressions match and the planner uses it. (measured: 6 ms indexed against
# 11-20 ms scanning, on a table small enough that the scan should have been winning)
ANN_TABLES = ("keyframe_embedding", "caption_embedding", "transcript_embedding")

CREATE_ANN_INDEX = """
    CREATE INDEX IF NOT EXISTS {name} ON {table}
    USING hnsw ((binary_quantize(embedding::halfvec({dims}))::bit({dims})) bit_hamming_ops)
    WITH (m = {m}, ef_construction = {ef_construction})
    WHERE model_id = {model_id};
"""

SELECT_MODELS_WITH_ROWS = """
    SELECT m.model_id, m.name, m.dims
    FROM embedding_model m
    WHERE EXISTS (SELECT 1 FROM {table} e WHERE e.model_id = m.model_id)
    ORDER BY m.model_id;
"""


def ann_index_name(model_id: int, table: str = "keyframe_embedding") -> str:
    return f"{table}_m{model_id}_bq_hnsw"

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

ANALYZE_TABLES = ["keyframe_embedding", "caption_embedding", "transcript_embedding",
                  "keyframe_text", "keyframe_caption", "transcript_segment"]


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


def _create_index(conn, sql, logger):
    """
    Builds one index, falling back to a serial build if the container cannot host a parallel one.

    A parallel HNSW build asks for a dynamic shared memory segment the size of
    maintenance_work_mem, and the pgvector image ships with a 63 MB /dev/shm, so with the 8 GB we
    ask for the very first build of a new index dies on `No space left on device` -- pointing at a
    disk that is not full. A serial build wants no shared segment at all and gets the same index,
    just slower, which beats not having one.

    The real fix is `--shm-size=8g` on the container; this is so a build never fails for it.
    """
    import psycopg2

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        return
    except psycopg2.errors.DiskFull:
        conn.rollback()

    logger.warning(
        "Parallel index build could not get shared memory (/dev/shm is too small for "
        "maintenance_work_mem). Falling back to a serial build -- slower, same index. "
        "Give the Postgres container a bigger --shm-size to get the parallel build back."
    )
    with conn.cursor() as cur:
        cur.execute("SET max_parallel_maintenance_workers = 0;")
        cur.execute(sql)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(f"SET max_parallel_maintenance_workers = {Config().PG_BUILD_PARALLEL_WORKERS};")


def build_indexes(conn, ann=True, text=True, analyze=True):
    """Creates the derived indexes. Safe to re-run; each statement is IF NOT EXISTS."""
    conf = Config()
    logger = custom_logger.get_logger("index_ops")

    apply_build_tuning(conn)

    if ann:
        for table in ANN_TABLES:
            for model_id, name, dims in embedded_models(conn, table):
                index = ann_index_name(model_id, table)
                logger.info(f"Building {index} for {name} ({dims}d) -- this is the long one...")
                sql = CREATE_ANN_INDEX.format(
                    name=index, table=table, dims=dims, model_id=model_id,
                    m=conf.HNSW_M, ef_construction=conf.HNSW_EF_CONSTRUCTION,
                )
                _create_index(conn, sql, logger)
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
        targets.extend(ann_index_name(m[0], table)
                       for table in ANN_TABLES for m in embedded_models(conn, table))
    if text:
        targets.extend(name for name, _ in TEXT_INDEXES)

    for name in targets:
        with conn.cursor() as cur:
            cur.execute(f"DROP INDEX IF EXISTS {name};")
        conn.commit()
        logger.info(f"Dropped {name}.")


def embedded_models(conn, table: str = "keyframe_embedding"):
    """Returns [(model_id, name, dims)] for models that actually have vectors in `table`."""
    if table not in ANN_TABLES:  # the name is interpolated, so it may only come from that list
        raise ValueError(f"{table} is not a vector table")
    with conn.cursor() as cur:
        cur.execute(SELECT_MODELS_WITH_ROWS.format(table=table))
        return cur.fetchall()


def index_status(conn):
    """Returns [(index_name, exists, size_pretty)] for reporting."""
    expected = ([ann_index_name(m[0], table)
                 for table in ANN_TABLES for m in embedded_models(conn, table)]
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

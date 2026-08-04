"""
The expensive derived indexes: the ANN vector index and the full-text GIN indexes.

Typical order of operations for a bulk load:

    climb-pipe index drop        # before a large COPY, if the indexes already exist
    ... load stages ...
    climb-pipe index build       # once, at the end
"""

import custom_logger
from config import Config

# Binary-quantized HNSW.
#
# Two reasons this is quantized rather than a plain halfvec index. First, size: at 12.4M
# keyframes a halfvec HNSW is ~28 GB, dangerosly close to a standard 32GB and way to much for a more compact
# 16GB device of usable RAM, while the bit(1024) version is ~4 GB and does. Second, the partial predicate
# does structural duty -- a search query that forgets `WHERE embedding IS NOT NULL` cannot use
# this index, so it announces itself as a seq scan instead of silently ranking un-embedded
# rows first the way `ORDER BY similarity DESC` used to.
KEYFRAMES_ANN_INDEX = "keyframes_bq_hnsw"
CREATE_KEYFRAMES_ANN_INDEX = """
    CREATE INDEX IF NOT EXISTS {name} ON keyframes
    USING hnsw ((binary_quantize(embedding)::bit(1024)) bit_hamming_ops)
    WITH (m = {m}, ef_construction = {ef_construction})
    WHERE embedding IS NOT NULL;
"""

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

ANALYZE_TABLES = ["keyframes", "keyframe_text", "keyframe_caption", "transcript_segment"]


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
        logger.info(f"Building {KEYFRAMES_ANN_INDEX} (this is the long one)...")
        with conn.cursor() as cur:
            cur.execute(CREATE_KEYFRAMES_ANN_INDEX.format(
                name=KEYFRAMES_ANN_INDEX,
                m=conf.HNSW_M,
                ef_construction=conf.HNSW_EF_CONSTRUCTION,
            ))
        conn.commit()
        logger.info(f"Built {KEYFRAMES_ANN_INDEX}.")

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
        targets.append(KEYFRAMES_ANN_INDEX)
    if text:
        targets.extend(name for name, _ in TEXT_INDEXES)

    for name in targets:
        with conn.cursor() as cur:
            cur.execute(f"DROP INDEX IF EXISTS {name};")
        conn.commit()
        logger.info(f"Dropped {name}.")


def index_status(conn):
    """Returns [(index_name, exists, size_pretty)] for reporting."""
    expected = [KEYFRAMES_ANN_INDEX] + [name for name, _ in TEXT_INDEXES]

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

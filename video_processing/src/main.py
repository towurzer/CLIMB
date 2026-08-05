"""
CLIMB pipeline entry point.

Subcommands rather than the previous chain of boolean flags, which had grown to the point where
`elif extract_keyframes is not extract_keyframes_no_db` was load-bearing.

A typical batch, start to finish:

    python main.py migrate
    python main.py enqueue --manifest videos.txt
    python main.py fetch
    python main.py ingest-shots
    python main.py decode
    python main.py select
    python main.py purge          # the raw video is dead weight from here on
    python main.py embed
    python main.py ocr
    python main.py caption
    python main.py asr
    python main.py embed-text
    python main.py index build    # once, at the end, on a machine with RAM to spare
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

import db_setup
import worker_http_endpoint
from config import Config
from custom_logger import setup_logging
from db.connection import connection_scope
from db.index_ops import build_indexes, drop_indexes, index_status
from db.migrate import migration_status, run_migrations
from pipeline import runner
from pipeline.asr import asr_pending
from pipeline.caption import caption_pending
from pipeline.decode import decode_from_database
from pipeline.embed import embed_pending
from pipeline.keyframe_selection import select_from_database
from pipeline.ocr import ocr_pending
from pipeline.shot_boundaries import ingest_directory
from pipeline.text_embed import embed_text_pending

# The order `run` applies stages in, regardless of the order they are listed on the command line.
RUN_ORDER = ["fetch", "ingest-shots", "decode", "select", "purge",
             "embed", "ocr", "caption", "asr", "embed-text"]


def add_common(parser, sharded=False):
    parser.add_argument("--collection", default=None,
                        help="restrict to one collection (default: Config.COLLECTION)")
    parser.add_argument("--limit", type=int, default=None)
    if sharded:
        parser.add_argument("--shard", type=int, default=0)
        parser.add_argument("--shards", type=int, default=1,
                            help="split the work N ways so several machines can run at once")
    return parser


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="CLIMB video processing pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="print the podman command for the database")
    sub.add_parser("migrate", help="apply pending schema migrations")
    sub.add_parser("serve", help="start the search engine HTTP worker")
    add_common(sub.add_parser("status", help="what is done and what is left"))

    enqueue = add_common(sub.add_parser("enqueue", help="load a manifest of videos into the queue"))
    enqueue.add_argument("--manifest", required=True)

    add_common(sub.add_parser("fetch", help="download queued videos"))
    add_common(sub.add_parser("ingest-shots", help="probe videos and load master shot boundaries"))
    add_common(sub.add_parser("decode", help="one FFmpeg pass: web video, candidates, audio"))
    add_common(sub.add_parser("select", help="pick keyframes and write images"))
    add_common(sub.add_parser("embed", help="SigLIP2 vectors (GPU)"), sharded=True)
    add_common(sub.add_parser("asr", help="Whisper transcripts (GPU)"), sharded=True)
    add_common(sub.add_parser("embed-text", help="embed transcripts and captions (GPU)"), sharded=True)

    for name, helptext in [("ocr", "read on-screen text (GPU)"),
                           ("caption", "describe each shot (GPU)")]:
        stage = add_common(sub.add_parser(name, help=helptext), sharded=True)
        stage.add_argument("--all-keyframes", action="store_true",
                           help="every keyframe rather than one per shot")

    purge = add_common(sub.add_parser("purge", help="delete transient files whose stage is done"))
    purge.add_argument("--keep-raw", action="store_true", help="do not delete downloaded videos")

    index = sub.add_parser("index", help="build, drop or inspect the search indexes")
    index.add_argument("action", choices=["build", "drop", "status"])

    run = add_common(sub.add_parser("run", help="run several stages in order"))
    run.add_argument("--stages", default="fetch,ingest-shots,decode,select,purge",
                     help=f"comma-separated; any of {','.join(RUN_ORDER)}")
    return parser


def dispatch(args, conn=None):
    conf = Config()
    collection = getattr(args, "collection", None) or conf.COLLECTION
    limit = getattr(args, "limit", None)
    shard = getattr(args, "shard", 0)
    shards = getattr(args, "shards", 1)
    scope = "all" if getattr(args, "all_keyframes", False) else None
    # getattr throughout: `run` rebuilds the namespace from its own parser, which does not carry
    # stage-specific arguments like --keep-raw. Reading them directly makes `run --stages purge`
    # die with an AttributeError.
    keep_raw = getattr(args, "keep_raw", False)
    dataset_dir = os.path.join(conf.DATA_DIR, conf.DATASET_FOLDER)
    raw_dir = str(Path(conf.WORK_DIR) / "raw")

    if args.command == "schema":
        return db_setup.get_container_command()
    if args.command == "serve":
        return worker_http_endpoint.start()

    if conn is None:
        with connection_scope() as owned:
            return dispatch(args, conn=owned)

    if args.command == "migrate":
        run_migrations(conn)
        for version, name, applied in migration_status(conn):
            print(f"  {version}  {name}  {'applied' if applied else 'PENDING'}")
    elif args.command == "status":
        runner.status(conn, collection)
    elif args.command == "enqueue":
        runner.enqueue(conn, getattr(args, 'manifest', None), collection)
    elif args.command == "fetch":
        runner.fetch(conn, limit=limit, collection=collection)
    elif args.command == "ingest-shots":
        run_migrations(conn)
        ingest_directory(conn, video_dir=raw_dir, extra_video_dirs=(dataset_dir,),
                         boundary_dir=os.path.join(dataset_dir, conf.SCENES_DIR),
                         collection=collection, limit=limit,
                         video_ids=runner.queued_video_ids(conn, collection))
    elif args.command == "decode":
        decode_from_database(conn, source_dir=dataset_dir, collection=collection, limit=limit)
    elif args.command == "select":
        select_from_database(conn, collection=collection, limit=limit)
    elif args.command == "embed":
        embed_pending(conn, collection=collection, limit=limit, shard=shard, shards=shards)
    elif args.command == "ocr":
        ocr_pending(conn, collection=collection, limit=limit, shard=shard, shards=shards, scope=scope)
    elif args.command == "caption":
        caption_pending(conn, collection=collection, limit=limit, shard=shard, shards=shards,
                        scope=scope)
    elif args.command == "asr":
        asr_pending(conn, collection=collection, limit=limit, shard=shard, shards=shards)
    elif args.command == "embed-text":
        embed_text_pending(conn, collection=collection, shard=shard, shards=shards)
    elif args.command == "purge":
        runner.purge(conn, collection=collection, keep_raw=keep_raw)
    elif args.command == "index":
        if args.action == "build":
            build_indexes(conn)
        elif args.action == "drop":
            drop_indexes(conn)
        for name, exists, size in index_status(conn):
            print(f"  {name:34s} {'built' if exists else 'missing':8s} {size}")
    elif args.command == "run":
        stages = [s.strip() for s in args.stages.split(",") if s.strip()]
        unknown = [s for s in stages if s not in RUN_ORDER]
        if unknown:
            raise SystemExit(f"unknown stage(s): {unknown}\nknown: {', '.join(RUN_ORDER)}")
        # Sorted into pipeline order: listing them the wrong way round should not try to select
        # keyframes from a video that has not been decoded yet.
        for stage in sorted(stages, key=RUN_ORDER.index):
            print(f"--- {stage} ---")
            dispatch(argparse.Namespace(**{**vars(args), "command": stage}), conn=conn)


if __name__ == "__main__":
    setup_logging()
    load_dotenv()
    dispatch(build_parser().parse_args())

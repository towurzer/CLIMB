import os
import sys

from dotenv import load_dotenv

import db_setup, embeddings_extraction, utils, worker_http_endpoint
from config import CLIConfig, Config
from custom_logger import setup_logging
from db.connection import connection_scope
from db.index_ops import build_indexes
from db.migrate import run_migrations
from pipeline.decode import decode_from_database
from pipeline.keyframe_selection import select_from_database
from pipeline.shot_boundaries import ingest_directory

if __name__ == '__main__':
    setup_logging()
    load_dotenv()
    cli_config = CLIConfig()
    argv = sys.argv

    show_database_creation_message = utils.has_flag(argv, cli_config.database_container_creation_flag)
    show_info_message = utils.has_flag(argv, cli_config.help_flags)
    extract_embeddings = utils.has_flag(argv, cli_config.extract_embeddings)
    start_embedding_worker = utils.has_flag(argv, cli_config.start_embedding_worker)
    migrate = utils.has_flag(argv, cli_config.migrate)
    build_search_indexes = utils.has_flag(argv, cli_config.build_indexes)
    ingest_shots = utils.has_flag(argv, cli_config.ingest_shots)
    decode = utils.has_flag(argv, cli_config.decode)
    select_keyframes = utils.has_flag(argv, cli_config.select_keyframes)

    anyFlag = (show_database_creation_message or extract_embeddings or start_embedding_worker
               or migrate or build_search_indexes or ingest_shots or decode or select_keyframes)

    if show_info_message or not anyFlag:
        print(cli_config.help_string)
    elif show_database_creation_message:
        db_setup.get_container_command()
    elif migrate:
        with connection_scope() as conn:
            run_migrations(conn)
    elif build_search_indexes:
        with connection_scope() as conn:
            build_indexes(conn)
    elif ingest_shots:
        conf = Config()
        dataset_dir = os.path.join(conf.DATA_DIR, conf.DATASET_FOLDER)
        with connection_scope() as conn:
            run_migrations(conn)
            ingest_directory(
                conn,
                video_dir=dataset_dir,
                boundary_dir=os.path.join(dataset_dir, conf.SCENES_DIR),
                collection=conf.COLLECTION,
            )
    elif decode:
        conf = Config()
        with connection_scope() as conn:
            decode_from_database(
                conn,
                source_dir=os.path.join(conf.DATA_DIR, conf.DATASET_FOLDER),
                collection=conf.COLLECTION,
            )
    elif select_keyframes:
        conf = Config()
        with connection_scope() as conn:
            select_from_database(conn, collection=conf.COLLECTION)
    elif extract_embeddings:
        embeddings_extraction.extract_and_store_embeddings()
    elif start_embedding_worker:
        worker_http_endpoint.start()

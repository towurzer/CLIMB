import os.path
from pathlib import Path
import psycopg2
import utils
import custom_logger
import keyframe_extraction
from config import Config
import db_queries


def get_container_command():
    conf = Config()
    logger = custom_logger.get_logger("db_setup")
    command = "echo \"Something went wrong\""
    try:
        command = f"""podman run --name {conf.DB_CONTAINER_NAME} \\
            -e POSTGRES_PASSWORD={conf.db_password} \\
            -e POSTGRES_DB={conf.db_name} \\
            -v postgres_data:{conf.DB_CONTAINER_MOUNT_PATH} \\
            -p {conf.db_port}:5432 \\
            -d docker.io/ankane/pgvector:latest"""

        logger.debug("Created Database Command")
    except ValueError as e:
        logger.error(e)

    print("To create and start a new podman container for the database run: \n\n")
    print(
        command
    )

    # Dynamically generate the activation hint using the actual .env variables
    try:
        print(f'\n\nActivate vector extension by running:\n\npodman exec -it {conf.DB_CONTAINER_NAME} psql -U postgres -d {conf.db_name} -c "CREATE EXTENSION IF NOT EXISTS vector;"')
    except ValueError:
        pass

def setup_database(conn):
    """Creates the necessary tables in PostgreSQL."""
    logger = custom_logger.get_logger("db_setup")
    with conn.cursor() as cur:
        cur.execute(db_queries.DBQueries.create_video_table)
        cur.execute(db_queries.DBQueries.create_shots_table)
        conn.commit()
    logger.info("Database tables created.")


def connect_to_database():
    conf = Config()
    logger = custom_logger.get_logger("db_connect")
    try:
        logger.debug("Connecting to PostgreSQL...")
        DB_CONFIG = {
            "dbname": conf.db_name,
            "user": "postgres",
            "password": conf.db_password,
            "host": conf.db_host,
            "port": conf.db_port
        }
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def populate_db_with_keyframes(do_db_population):
    conf = Config()
    dataset_dir = os.path.join(conf.DATA_DIR, conf.DATASET_FOLDER)
    scenes_dir = os.path.join(dataset_dir, conf.SCENES_DIR)
    keyframe_dir = os.path.join(conf.DATA_DIR, conf.KEYFRAME_FOLDER)
    
    utils.create_dir(scenes_dir)
    utils.create_dir(keyframe_dir)
    db_connect_logger = custom_logger.get_logger("db_connect")
    logger = custom_logger.get_logger("keyframe_extraction")

    # Connect to Postgres
    conn = connect_to_database()
    if not conn:
        return

    db_connect_logger.info("Database connected")

    setup_database(conn)

    scene_files = list(Path(scenes_dir).glob('*.txt'))

    if not scene_files:
        logger.warn(f"No scene text files found in {scenes_dir}. Check directory path.")
        return

    for scene_file in scene_files:
        video_id = scene_file.stem.split('.')[0]
        video_stem = scene_file.stem.split('.')[1]

        video_path = Path(dataset_dir) / f"{video_id}.{video_stem}"
        if video_path.exists():
            keyframe_extraction.process_video_and_shots(conn, video_id, video_path, scene_file, do_db_population)
        else:
            logger.warn(f"Could not find video file for {video_id} in {dataset_dir}")

    conn.close()
    logger.info("All done! Keyframes extracted and PostgreSQL populated.")



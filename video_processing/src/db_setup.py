import custom_logger
from config import Config


def get_container_command():
    conf = Config()
    logger = custom_logger.get_logger("db_setup")
    command = "echo \"Something went wrong\""
    try:
        command = f"""podman run --name {conf.DB_CONTAINER_NAME} \\
            -e POSTGRES_PASSWORD={conf.db_password} \\
            -e POSTGRES_DB={conf.db_name} \\
            -e POSTGRES_USER={conf.db_user} \\
            -v postgres_data:{conf.DB_CONTAINER_MOUNT_PATH} \\
            -p {conf.db_port}:5432 \\
            -d {conf.DB_IMAGE} \\
            postgres \\
              -c shared_buffers={conf.PG_SERVE_SHARED_BUFFERS} \\
              -c effective_cache_size={conf.PG_SERVE_EFFECTIVE_CACHE_SIZE} \\
              -c work_mem={conf.PG_SERVE_WORK_MEM} \\
              -c max_parallel_maintenance_workers={conf.PG_BUILD_PARALLEL_WORKERS}"""

        logger.debug("Created Database Command")
    except ValueError as e:
        logger.error(e)

    print("To create and start a new podman container for the database run: \n\n")
    print(command)

    # The vector/pg_trgm/unaccent extensions used to need a manual psql invocation here.
    # Migration 001 creates them, so the only follow-up step is running the migrations.
    print("\n\nThen create the schema by running:\n\n    python src/main.py --migrate")

    print(
        f"\n\nNote: {conf.DB_IMAGE} is PostgreSQL 17. A data volume written by the previous "
        f"image (ankane/pgvector, PostgreSQL 16) cannot be reused across a major version, and "
        f"neither can dataset/climb_db.dump. Use a fresh volume name if 'postgres_data' still "
        f"holds the old cluster."
    )

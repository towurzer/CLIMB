import subprocess

import utils
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
            -v postgres_data:{conf.DB_CONTAINER_MOUNT_PATH} \\
            -p 5432:5432 \\
            -d docker.io/library/postgres"""

        logger.debug("Created Database Command")
    except ValueError as e:
        logger.error(e)

    print("To create and start a new podman container for the database run: \n\n")
    print(
        command
    )

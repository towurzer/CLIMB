import logging
import os

from config import Config
import utils

# Libraries that log every HTTP request (httpx at INFO, httpcore and urllib3 at DEBUG), every PNG
# chunk (PIL) or every lock (filelock). Their warnings and errors still come through.
QUIET_LIBRARIES = ("httpx", "httpcore", "urllib3", "PIL", "filelock")


def quiet_libraries():
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_logging():
    quiet_libraries()
    utils.create_dir(Config.LOG_FOLDER)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = logging.FileHandler(os.path.join(Config.LOG_FOLDER, Config.LOG_FILE))
    file_handler.setFormatter(formatter)

    error_file_handler = logging.FileHandler(os.path.join(Config.LOG_FOLDER, Config.ERROR_FILE))
    error_file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(Config.LOG_LEVEL_MIN)

    file_handler.setLevel(Config.LOG_LEVEL_FILE)
    error_file_handler.setLevel(Config.LOG_LEVEL_ERROR)
    console_handler.setLevel(Config.LOG_LEVEL_CONSOLE)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_file_handler)
    root_logger.addHandler(console_handler)


def setup_worker_logging():
    """
    Configures logging inside a pool worker.

    Workers are started with forkserver, so they re-import this module rather than inheriting the
    parent's handlers. Without this the root logger has none, and Python's lastResort fallback
    prints bare unformatted text and drops everything below WARNING on the floor.

    Console only: several workers appending to one log file concurrently is a good way to get
    interleaved lines. Their output goes to the parent's stderr instead.
    """
    quiet_libraries()
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    handler.setLevel(Config.LOG_LEVEL_CONSOLE)
    root_logger.setLevel(Config.LOG_LEVEL_MIN)
    root_logger.addHandler(handler)


def get_logger(tag):
    return logging.getLogger(tag.upper())
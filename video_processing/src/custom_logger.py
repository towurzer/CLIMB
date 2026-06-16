import logging
import os

from config import Config
import utils


def setup_logging():
    utils.create_dir(Config.LOG_FOLDER)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = logging.FileHandler(os.path.join(Config.LOG_FOLDER, Config.LOG_FILE))
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(Config.LOG_LEVEL_MIN)

    file_handler.setLevel(Config.LOG_LEVEL_FILE)
    console_handler.setLevel(Config.LOG_LEVEL_CONSOLE)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)



def get_logger(tag):
    return logging.getLogger(tag.upper())


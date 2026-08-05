import os


def create_dir(dir_path):
    """
    Creates the necessary project directories if they do not already exist.
    :param dir_path: path to the directory to create
    """
    os.makedirs(dir_path, exist_ok=True)



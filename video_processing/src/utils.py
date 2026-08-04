import os


def create_dir(dir_path):
    """
    Creates the necessary project directories if they do not already exist.
    :param dir_path: path to the directory to create
    """
    os.makedirs(dir_path, exist_ok=True)


def has_flag(argv, flags):
    return any(flag in argv for flag in flags)


def int_arg(argv, name, default=0):
    """Reads `--name value` from argv."""
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            try:
                return int(argv[index + 1])
            except ValueError:
                pass
    return default

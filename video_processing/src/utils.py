import os
import matplotlib.pyplot as plt


def create_dir(dir_path):
    """
    Creates the necessary project directories if they do not already exist.
    :param dir_path: path to the directory to create
    """
    os.makedirs(dir_path, exist_ok=True)

def has_flag(argv, flags):
    return any(flag in argv for flag in flags)

def graceful_exit():
    """Wait for all pots to be closed before exiting"""
    while plt.get_fignums():
        plt.pause(0.1)

    exit(0)

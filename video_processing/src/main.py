import sys

from dotenv import load_dotenv

import utils, dataset_compression, db_setup
from config import CLIConfig
from custom_logger import setup_logging

if __name__ == '__main__':
    setup_logging()
    load_dotenv()
    cli_config = CLIConfig()
    argv = sys.argv

    compress = utils.has_flag(argv, cli_config.compression_flags)
    show_database_creation_message = utils.has_flag(argv, cli_config.database_container_creation_flag)
    show_info_message = utils.has_flag(argv, cli_config.help_flags)

    anyFlag = compress or show_database_creation_message

    if show_info_message or not anyFlag:
        print(cli_config.help_string)
    elif compress:
        dataset_compression.compress()
    elif show_database_creation_message:
        db_setup.get_container_command()
    else:
        pass
        # main(do_pre_training_inference, do_training, do_post_training_inference, do_results_evaluation)

    utils.graceful_exit()

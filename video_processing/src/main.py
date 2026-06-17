import sys

from dotenv import load_dotenv

import utils, dataset_compression, db_setup, embeddings_extraction
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
    extract_keyframes = utils.has_flag(argv, cli_config.extract_keyframes)
    extract_embeddings = utils.has_flag(argv, cli_config.extract_embeddings)

    anyFlag = compress or show_database_creation_message or extract_keyframes or extract_embeddings

    if show_info_message or not anyFlag:
        print(cli_config.help_string)
    elif compress:
        dataset_compression.compress()
    elif show_database_creation_message:
        db_setup.get_container_command()
    elif extract_keyframes:
        db_setup.populate_db_with_keyframes()
    elif extract_embeddings:
        embeddings_extraction.extract_and_store_embeddings()
    else:
        pass
        # main(do_pre_training_inference, do_training, do_post_training_inference, do_results_evaluation)

    utils.graceful_exit()

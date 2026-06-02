import sys

import utils, dataset_compression
from config import CLIConfig

if __name__ == '__main__':
    cli_config = CLIConfig()
    argv = sys.argv

    compress = utils.has_flag(argv, cli_config.compression_flags)
    show_info_message = utils.has_flag(argv, cli_config.help_flags)

    anyFlag = compress

    if show_info_message or not anyFlag:
        print(cli_config.help_string)
    elif compress:
        dataset_compression.compress()
    else:
        pass
        # main(do_pre_training_inference, do_training, do_post_training_inference, do_results_evaluation)

    utils.graceful_exit()

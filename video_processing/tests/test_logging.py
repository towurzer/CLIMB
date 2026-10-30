"""
Which libraries are kept below WARNING.

The root logger runs at DEBUG, so without this every Hugging Face request prints an httpx INFO line
on the console and every PNG fills the log file with PIL chunk lines.
"""

import logging

import custom_logger
from config import Config


def test_noisy_libraries_only_pass_warnings(monkeypatch):
    for name in custom_logger.QUIET_LIBRARIES:
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    custom_logger.setup_worker_logging()
    for name in ("httpx", "httpcore.http11", "urllib3.connectionpool", "PIL.PngImagePlugin", "filelock"):
        logger = logging.getLogger(name)
        assert not logger.isEnabledFor(logging.INFO), name
        assert logger.isEnabledFor(logging.WARNING), name


def test_climb_loggers_keep_the_root_level():
    custom_logger.setup_worker_logging()
    assert custom_logger.get_logger("runner").level == logging.NOTSET


def test_warnings_go_once_to_the_log_and_to_the_error_log(monkeypatch, tmp_path):
    root = logging.getLogger()
    monkeypatch.setattr(Config, "LOG_FOLDER", str(tmp_path))
    monkeypatch.setattr(root, "level", root.level)
    before = list(root.handlers)
    custom_logger.setup_logging()
    try:
        log = custom_logger.get_logger("probe")
        log.info("an info line")
        log.error("an error line")
    finally:
        for handler in [h for h in root.handlers if h not in before]:
            root.removeHandler(handler)
            handler.close()

    log_text = (tmp_path / Config.LOG_FILE).read_text()
    error_text = (tmp_path / Config.ERROR_FILE).read_text()
    assert log_text.count("an info line") == 1
    assert log_text.count("an error line") == 1
    assert error_text.count("an error line") == 1
    assert "an info line" not in error_text

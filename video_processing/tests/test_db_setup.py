"""
Which address the database container publishes its port on.

Published on every interface, the database answers anyone who can reach the machine, and on a shared
server that is more people than the team. Nothing fails when that happens, so it is pinned here.
"""

import os

import db_setup
import pytest
from config import Config


def published(command):
    """The value of the command's -p option; the rest of it carries the password."""
    return next(line.split()[1] for line in command.splitlines() if line.strip().startswith("-p "))


@pytest.mark.skipif(bool(os.getenv("CLIMB_DB_BIND")), reason="CLIMB_DB_BIND is set on this machine")
def test_the_port_answers_on_this_machine_only_by_default():
    assert published(db_setup.container_command()) == f"127.0.0.1:{Config().db_port}:5432"


def test_the_address_comes_from_the_config(monkeypatch):
    conf = Config()
    conf.DB_BIND_ADDRESS = "0.0.0.0"
    monkeypatch.setattr(db_setup, "Config", lambda: conf)
    assert published(db_setup.container_command("docker")) == f"0.0.0.0:{conf.db_port}:5432"

"""Pytest configuration and fixtures."""

import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path_factory, monkeypatch):
    """Point the user config at a temp file for every test.

    `Config()` reads it and `/vim` writes it, so without this the suite reads
    and rewrites the developer's own `~/.config/oi/config.json`.
    """
    config_path = tmp_path_factory.mktemp("user-config") / "config.json"
    monkeypatch.setattr("oi.config.settings.get_user_config_path", lambda: config_path)
    return config_path


@pytest.fixture
def temp_config_dir():
    """Provide a temporary directory for config files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

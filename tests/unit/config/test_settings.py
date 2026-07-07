import os
from unittest.mock import patch

from oi.config.settings import load_env_file


def _load_env_file_in(tmp_path):
    with patch("oi.config.settings.user_config_dir", return_value=str(tmp_path)):
        load_env_file()


class TestLoadEnvFile:
    def test_overrides_inherited_environment(self, tmp_path, monkeypatch):
        (tmp_path / "env").write_text("OPENROUTER_API_KEY=oi-key\n")
        monkeypatch.setenv("OPENROUTER_API_KEY", "global-key")

        _load_env_file_in(tmp_path)

        assert os.environ["OPENROUTER_API_KEY"] == "oi-key"

    def test_unset_keys_fall_back_to_environment(self, tmp_path, monkeypatch):
        (tmp_path / "env").write_text("OPENROUTER_API_KEY=oi-key\n")
        monkeypatch.setenv("OPENROUTER_API_KEY", "other-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "global-key")

        _load_env_file_in(tmp_path)

        assert os.environ["ANTHROPIC_API_KEY"] == "global-key"

    def test_missing_file_is_a_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "global-key")

        _load_env_file_in(tmp_path)

        assert os.environ["OPENROUTER_API_KEY"] == "global-key"
        assert not (tmp_path / "env").exists()

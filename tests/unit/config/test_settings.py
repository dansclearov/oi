import json
import os
from unittest.mock import patch

import pytest

from oi.config.settings import (
    load_env_file,
    load_user_config,
    save_user_config,
    update_user_config,
)


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


class TestSaveUserConfig:
    """`isolated_user_config` (conftest) points the path at a temp file."""

    def test_round_trips_and_keeps_other_keys(self, isolated_user_config):
        save_user_config({"vim_mode": True, "tui": True})
        update_user_config("vim_mode", False)

        assert load_user_config() == {"vim_mode": False, "tui": True}

    def test_the_file_is_swapped_in_whole(self, isolated_user_config, monkeypatch):
        save_user_config({"vim_mode": True})
        # The reader must never observe the new content until it is complete.
        seen = []

        real_replace = os.replace

        def spy(src, dst):
            seen.append(json.loads(dst.read_text()))
            real_replace(src, dst)

        monkeypatch.setattr("oi.config.settings.os.replace", spy)
        save_user_config({"vim_mode": False})

        assert seen == [{"vim_mode": True}]
        assert load_user_config() == {"vim_mode": False}

    def test_a_failed_write_raises_and_leaves_no_debris(
        self, isolated_user_config, monkeypatch
    ):
        save_user_config({"vim_mode": True})
        monkeypatch.setattr(
            "oi.config.settings.os.replace",
            lambda src, dst: (_ for _ in ()).throw(OSError("disk full")),
        )

        with pytest.raises(OSError, match="disk full"):
            save_user_config({"vim_mode": False})

        assert load_user_config() == {"vim_mode": True}
        assert list(isolated_user_config.parent.iterdir()) == [isolated_user_config]

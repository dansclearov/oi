"""Configuration for LLM CLI."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from platformdirs import user_config_dir, user_data_dir


def get_env_file_path() -> Path:
    """Get the path to oi's own env file (API keys)."""
    return Path(user_config_dir("oi", ensure_exists=True)) / "env"


def load_env_file() -> None:
    """Load oi's env file over the inherited environment, if it exists."""
    load_dotenv(get_env_file_path(), override=True)


def get_user_config_path() -> Path:
    """Get the path to the user configuration file."""
    config_dir = Path(user_config_dir("oi", ensure_exists=True))
    return config_dir / "config.json"


def load_user_config() -> dict[str, Any]:
    """Load user configuration from file."""
    config_path = get_user_config_path()

    if not config_path.exists():
        return {}

    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_user_config(config_data: dict[str, Any]) -> None:
    """Save user configuration, swapping the file in whole.

    Truncating in place would let a concurrent reader (or a crash mid-write)
    see a partial file, which `load_user_config` reads back as `{}` — every
    setting silently reverted to its default.

    Raises `OSError` if the config can't be written; callers say so rather
    than reporting a setting as saved when it wasn't.
    """
    config_path = get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    fd, name = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
    tmp_path = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if config_path.exists():
            os.chmod(tmp_path, config_path.stat().st_mode & 0o777)
        else:
            os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, config_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def update_user_config(key: str, value: Any) -> None:
    """Update a specific key in the user configuration. Raises `OSError`."""
    config = load_user_config()
    config[key] = value
    save_user_config(config)


@dataclass
class Config:
    chat_dir: str = field(
        default_factory=lambda: os.getenv(
            "OI_CHAT_DIR",
            str(Path(user_data_dir("oi", ensure_exists=True)) / "chats"),
        )
    )
    vim_mode: bool = field(
        default_factory=lambda: load_user_config().get("vim_mode", False)
    )
    tui: bool = field(default_factory=lambda: load_user_config().get("tui", True))

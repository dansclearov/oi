"""Configuration for LLM CLI."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir


def get_user_config_path() -> Path:
    """Get the path to the user configuration file."""
    config_dir = Path(user_config_dir("llm_cli", ensure_exists=True))
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
    """Save user configuration to file."""
    config_path = get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)
    except OSError:
        pass


def update_user_config(key: str, value: Any) -> None:
    """Update a specific key in the user configuration."""
    config = load_user_config()
    config[key] = value
    save_user_config(config)


@dataclass
class Config:
    chat_dir: str = field(
        default_factory=lambda: os.getenv(
            "LLM_CLI_CHAT_DIR",
            str(Path(user_data_dir("llm_cli", ensure_exists=True)) / "chats"),
        )
    )
    vim_mode: bool = field(
        default_factory=lambda: load_user_config().get("vim_mode", False)
    )

import sys

import pytest

from llm_cli.cli import parse_arguments
from llm_cli.registry import ModelRegistry


def test_parse_arguments_rejects_unknown_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("llm_cli.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("llm_cli.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["llm-cli", "unknown-prompt"])

    with pytest.raises(SystemExit) as exc_info:
        parse_arguments(registry)

    assert isinstance(exc_info.value, SystemExit)
    assert exc_info.value.code == 2


def test_parse_arguments_accepts_known_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("llm_cli.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("llm_cli.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["llm-cli", "concise", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.prompt == "concise"
    assert args.model == "sonnet"


def test_parse_arguments_uses_configured_default_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("llm_cli.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr(
        "llm_cli.cli.load_user_config", lambda: {"default_prompt": "concise"}
    )
    monkeypatch.setattr(sys, "argv", ["llm-cli", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.prompt == "concise"
    assert args.model == "sonnet"


def test_parse_arguments_falls_back_when_configured_default_prompt_is_unknown(
    monkeypatch,
):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("llm_cli.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr(
        "llm_cli.cli.load_user_config", lambda: {"default_prompt": "missing"}
    )
    monkeypatch.setattr(sys, "argv", ["llm-cli", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.prompt == "general"

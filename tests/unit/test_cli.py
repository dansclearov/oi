import sys

import pytest

from oi.cli import parse_arguments
from oi.registry import ModelRegistry


def test_parse_arguments_rejects_unknown_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi", "-P", "unknown-prompt"])

    with pytest.raises(SystemExit) as exc_info:
        parse_arguments(registry)

    assert isinstance(exc_info.value, SystemExit)
    assert exc_info.value.code == 2


def test_parse_arguments_accepts_known_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi", "-P", "concise", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.system_prompt == "concise"
    assert args.model == "sonnet"


def test_parse_arguments_uses_configured_default_prompt(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr(
        "oi.cli.load_user_config", lambda: {"default_prompt": "concise"}
    )
    monkeypatch.setattr(sys, "argv", ["oi", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.system_prompt == "concise"
    assert args.model == "sonnet"


def test_parse_arguments_falls_back_when_configured_default_prompt_is_unknown(
    monkeypatch,
):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr(
        "oi.cli.load_user_config", lambda: {"default_prompt": "missing"}
    )
    monkeypatch.setattr(sys, "argv", ["oi", "-m", "sonnet"])

    args = parse_arguments(registry)

    assert args.system_prompt == "empty"


def test_parse_arguments_headless_prompt_and_ephemeral(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi", "-p", "hello there", "--ephemeral"])

    args = parse_arguments(registry)

    assert args.prompt == "hello there"
    assert args.ephemeral is True


def test_parse_arguments_defaults_for_headless_flags(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi"])

    args = parse_arguments(registry)

    assert args.prompt is None
    assert args.ephemeral is False
    assert args.command is None


def test_parse_arguments_stats_subcommand(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi", "stats", "--deep"])

    args = parse_arguments(registry)

    assert args.command == "stats"
    assert args.deep is True


def test_parse_arguments_stats_without_deep(monkeypatch):
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_display_models", lambda: ["sonnet"])
    monkeypatch.setattr(registry, "get_default_model", lambda: "sonnet")
    monkeypatch.setattr("oi.cli.get_prompts", lambda: ["general", "concise"])
    monkeypatch.setattr("oi.cli.load_user_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["oi", "stats"])

    args = parse_arguments(registry)

    assert args.command == "stats"
    assert args.deep is False

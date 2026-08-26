"""Startup cost guards."""

import subprocess
import sys


def test_startup_path_does_not_import_pydantic_ai():
    """pydantic_ai's package __init__ costs ~600ms, so both frontends keep it
    off the import path (it's pre-imported on a background thread instead).
    A module-level `from pydantic_ai...` import anywhere in the startup chain
    regresses every launch; this catches it.
    """
    code = (
        "import sys\n"
        "import oi.app\n"
        "import oi.tui.app\n"
        "loaded = [m for m in sys.modules if m.startswith('pydantic_ai')]\n"
        "sys.exit(f'pydantic_ai imported at startup: {loaded}' if loaded else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_ensure_does_not_race_the_warmup_thread():
    """A plain pydantic_ai import racing the warm-up thread crashes on
    pydantic_ai's internal import cycles ("partially initialized module");
    `ensure()` must serialize against the in-flight warm-up instead.
    """
    code = (
        "from oi import warmup\n"
        "warmup.warm()\n"
        "warmup.ensure()\n"
        "from pydantic_ai.messages import ModelMessage\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_a_new_chat_tui_mount_does_not_import_pydantic_ai():
    """A new chat's replay runs on the main thread ahead of the warm-up
    thread, so any pydantic_ai import there lands before the first paint."""
    code = (
        "import asyncio, sys\n"
        "from unittest.mock import Mock\n"
        "import oi.warmup\n"
        "oi.warmup.warm = lambda: None\n"
        "from oi.app import ChatLoopContext\n"
        "from oi.config.settings import Config\n"
        "from oi.core.chat_manager import ChatManager\n"
        "from oi.llm_types import ChatOptions, ModelCapabilities\n"
        "from oi.tui.app import OiApp\n"
        "config = Config(chat_dir=sys.argv[1])\n"
        "manager = ChatManager(config)\n"
        "chat = manager.create_new_chat('m', 'prompt')\n"
        "client = Mock()\n"
        "client.resolve_capabilities.return_value = ModelCapabilities()\n"
        "registry = Mock()\n"
        "registry.get_provider_for_model.return_value = ('anthropic', 'x')\n"
        "ctx = ChatLoopContext(config=config, chat_manager=manager,\n"
        "    llm_client=client, input_handler=Mock(), chat_options=ChatOptions(),\n"
        "    prompt_str='prompt', active_model='m')\n"
        "async def run():\n"
        "    async with OiApp(chat, ctx, registry, is_new_chat=True).run_test():\n"
        "        pass\n"
        "asyncio.run(run())\n"
        "loaded = [m for m in sys.modules if m.startswith('pydantic_ai')]\n"
        "sys.exit(f'pydantic_ai imported by the mount: {loaded}' if loaded else 0)\n"
    )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [sys.executable, "-c", code, tmp], capture_output=True, text=True
        )
    assert result.returncode == 0, result.stderr

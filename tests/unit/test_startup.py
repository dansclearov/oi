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

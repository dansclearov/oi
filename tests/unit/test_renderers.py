"""Tests for streaming response renderers."""

import io

from rich.console import Console

from llm_cli.llm_types import ChatOptions, ModelCapabilities
from llm_cli.renderers import StyledRenderer


def _make_renderer() -> tuple[StyledRenderer, io.StringIO]:
    capabilities = ModelCapabilities(supports_thinking=True)
    options = ChatOptions(show_assistant_label=False)
    renderer = StyledRenderer(capabilities, options)
    buffer = io.StringIO()
    # Override the console so output is captured without ANSI styling.
    renderer.console = Console(file=buffer, no_color=True, highlight=False)
    return renderer, buffer


def test_thinking_trailing_newlines_collapse_to_single_separator():
    """Trailing newlines from a thinking trace (e.g. Gemini) must not stack
    with the separator that ends the thinking section."""
    renderer, buffer = _make_renderer()

    renderer.start_response()
    # Provider streams thinking with its own trailing newlines.
    renderer.render_thinking("reasoning here\n\n\n")
    renderer.close_thinking_section(final=True)
    renderer.render_text("the answer")
    renderer.finish_response()

    output = buffer.getvalue()
    assert output == "reasoning here\nthe answer\n"


def test_thinking_internal_blank_lines_preserved():
    """Blank lines between thinking chunks are kept; only the trailing run
    before the boundary is dropped."""
    renderer, buffer = _make_renderer()

    renderer.start_response()
    renderer.render_thinking("first thought\n\n")
    renderer.render_thinking("second thought\n\n")
    renderer.close_thinking_section(final=True)
    renderer.render_text("answer")
    renderer.finish_response()

    output = buffer.getvalue()
    assert output == "first thought\n\nsecond thought\nanswer\n"

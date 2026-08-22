"""Formatting of native tool call lines across provider arg shapes."""

from oi.tui.tool_lines import (
    describe_call,
    describe_return,
    recover_args_from_return,
    tool_line_text,
)


def test_web_search_query_anthropic_xai():
    assert (
        describe_call("web_search", {"query": "cats", "num_results": "10"})
        == 'Web Search("cats")'
    )


def test_web_search_queries_google():
    assert (
        describe_call("web_search", {"queries": ["cats", "dogs"]})
        == 'Web Search("cats", "dogs")'
    )


def test_web_search_openai_actions():
    assert (
        describe_call("web_search", {"type": "search", "query": "cats"})
        == 'Web Search("cats")'
    )
    assert (
        describe_call("web_search", {"type": "open_page", "url": "https://x.io/a"})
        == "Fetch(https://x.io/a)"
    )
    assert (
        describe_call(
            "web_search", {"type": "find", "url": "https://x.io", "pattern": "v2"}
        )
        == 'Find("v2" in https://x.io)'
    )


def test_web_fetch_url_shapes():
    assert describe_call("web_fetch", {"url": "https://x.io"}) == "Fetch(https://x.io)"
    assert (
        describe_call("web_fetch", {"urls": ["https://a.io", "https://b.io"]})
        == "Fetch(https://a.io, https://b.io)"
    )


def test_code_execution_variants():
    assert describe_call("code_execution", {"command": "grep -c cats results.txt"}) == (
        "Code(grep -c cats results.txt)"
    )
    assert describe_call(
        "code_execution", {"command": "view", "path": "/tmp/r.json"}
    ) == ("Code(view /tmp/r.json)")
    assert describe_call("code_execution", {"code": "print(1)\nprint(2)"}) == (
        "Code(print(1))"
    )
    # Imports and comments say nothing about what the code does; show the
    # first line that acts. All-boilerplate code falls back to its first line.
    assert (
        describe_call(
            "code_execution",
            {"code": "import json\n# parse it\nr1p = json.loads(r1)"},
        )
        == "Code(r1p = json.loads(r1))"
    )
    assert describe_call("code_execution", {"code": "import json"}) == (
        "Code(import json)"
    )


def test_fetch_url_recovered_from_return_content():
    # A fetch driven from inside a code-execution block carries no args of
    # its own, but the return payload names the retrieved page.
    content = {"type": "web_fetch_result", "url": "https://x.io/a", "content": {}}
    assert recover_args_from_return("web_fetch", content) == {"url": "https://x.io/a"}
    assert recover_args_from_return("web_search", [{}, {}]) is None
    assert recover_args_from_return("web_fetch", None) is None


def test_unknown_args_are_pending():
    assert describe_call("web_search", None) is None
    assert describe_call("web_search", {}) is None


def test_long_query_is_truncated():
    described = describe_call("web_search", {"query": "cats " * 40})
    assert described is not None
    assert len(described) < 100
    assert "..." in described


def test_return_summary_counts_results():
    assert describe_return("web_search", [{}, {}, {}]) == "3 results"
    assert describe_return("web_search", [{}]) == "1 result"
    assert describe_return("web_search", None) is None
    assert describe_return("web_fetch", {"url": "https://x.io"}) is None


def test_line_text_states():
    pending = tool_line_text("web_search", None)
    assert pending.plain == "● Web Search…"

    resolved = tool_line_text("web_search", {"query": "cats"}, "2 results")
    assert resolved.plain == '● Web Search("cats") · 2 results'

    # Done without args (e.g. a search driven from a code-execution block):
    # no running-ellipsis, but the completion note still shows.
    argless_done = tool_line_text("web_search", None, "10 results", done=True)
    assert argless_done.plain == "● Web Search · 10 results"

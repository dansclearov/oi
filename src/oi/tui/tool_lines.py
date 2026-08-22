"""Formatting for server-side (native) tool call lines in the TUI.

One line per call, Claude-Code-style: a green marker plus a
calling-a-function description — `Web Search("query")`, `Fetch(url)`,
`Code(command)`. Providers differ in arg shapes (Anthropic/xAI send `query`,
Google `queries`, OpenAI Responses one `web_search` tool with a typed action
dict), so the description is derived per shape rather than per provider.
"""

from typing import Any, Optional, cast

from rich.text import Text

from oi.text import truncate_to_cells

TOOL_MARKER = "● "
_MARKER_STYLE = "green"
_PENDING_STYLE = "bright_black"
_SUFFIX_STYLE = "bright_black"

_DETAIL_MAX_CELLS = 80

_TITLES = {
    "web_search": "Web Search",
    "web_fetch": "Fetch",
    "code_execution": "Code",
}


def tool_line_text(
    tool_name: str,
    args: Optional[dict[str, Any]],
    suffix: Optional[str] = None,
    *,
    done: bool = False,
) -> Text:
    """The full line for one call: marker, description, optional result note."""
    text = Text()
    text.append(TOOL_MARKER, style=_MARKER_STYLE)
    description = describe_call(tool_name, args)
    if description is None and not done:
        # Args not streamed yet: show the call is running.
        text.append(f"{_title(tool_name)}…", style=_PENDING_STYLE)
    elif description is None:
        # Completed without derivable args (e.g. a search invoked from inside
        # a code-execution block carries its query in the code, not the call).
        text.append(_title(tool_name))
    else:
        text.append(description)
    if suffix:
        text.append(f" · {suffix}", style=_SUFFIX_STYLE)
    return text


def describe_call(tool_name: str, args: Optional[dict[str, Any]]) -> Optional[str]:
    """`Title(interesting args)` for a call, or None while args are unknown."""
    if not isinstance(args, dict) or not args:
        return None
    if tool_name == "web_search":
        return _describe_web_search(args)
    if tool_name == "web_fetch":
        return _describe_web_fetch(args)
    if tool_name == "code_execution":
        return _describe_code_execution(args)
    return _title(tool_name)


def describe_return(tool_name: str, content: object) -> Optional[str]:
    """A short result note, when one is derivable from the return content."""
    if tool_name == "web_search" and isinstance(content, list):
        count = len(content)
        return f"{count} result{'s' if count != 1 else ''}"
    return None


def _title(tool_name: str) -> str:
    return _TITLES.get(tool_name, tool_name)


def _describe_web_search(args: dict[str, Any]) -> Optional[str]:
    # OpenAI Responses folds fetching into web_search as typed actions.
    action = args.get("type")
    if action == "open_page" and args.get("url"):
        return f"Fetch({args['url']})"
    if action == "find" and args.get("url"):
        pattern = _shorten(str(args.get("pattern", "")))
        return f'Find("{pattern}" in {args["url"]})'
    queries = args.get("queries")
    if isinstance(queries, list) and queries:
        quoted = ", ".join(f'"{_shorten(str(q))}"' for q in queries)
        return f"Web Search({quoted})"
    if args.get("query"):
        return f'Web Search("{_shorten(str(args["query"]))}")'
    return None


def _describe_web_fetch(args: dict[str, Any]) -> Optional[str]:
    urls = args.get("urls")
    if isinstance(urls, list) and urls:
        return f"Fetch({', '.join(str(url) for url in urls)})"
    if args.get("url"):
        return f"Fetch({args['url']})"
    return None


def _describe_code_execution(args: dict[str, Any]) -> Optional[str]:
    # Anthropic's bash variant sends `command`, the text-editor variant
    # `command` + `path` (view, str_replace, ...).
    command = args.get("command")
    if command:
        path = args.get("path")
        detail = f"{command} {path}" if path else str(command)
        return f"Code({_shorten(detail)})"
    code = args.get("code")
    if code:
        line = _first_meaningful_line(str(code))
        return f"Code({_shorten(line)})" if line else None
    return None


def _first_meaningful_line(code: str) -> Optional[str]:
    """The first line that says what the code *does* — dynamic-filtering
    snippets routinely open with imports, which describe nothing."""
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith(("import ", "from ", "#")):
            return line
    return lines[0] if lines else None


def recover_args_from_return(
    tool_name: str, content: object
) -> Optional[dict[str, Any]]:
    """Display args salvaged from the return payload, for calls that carried
    none — a fetch driven from inside a code-execution block keeps its URL in
    the code, but the result still names the page it retrieved."""
    if tool_name == "web_fetch" and isinstance(content, dict):
        url = cast("dict[str, Any]", content).get("url")
        if url:
            return {"url": url}
    return None


def _shorten(text: str) -> str:
    return truncate_to_cells(" ".join(text.split()), _DETAIL_MAX_CELLS)

"""Shared label definitions and formatting helpers."""

from dataclasses import dataclass

from colored import attr, fg
from prompt_toolkit.formatted_text import HTML
from rich.text import Text


@dataclass(frozen=True)
class LabelStyle:
    text: str
    ansi_style: str
    rich_style: str
    prompt_html_color: str | None = None


RESET_ANSI = attr("reset")

# Inline pill styling for paste/image placeholders. PT_STYLE is the
# prompt_toolkit fragment style (used by `PillProcessor` during input);
# `ansi_pill` wraps text in equivalent ANSI codes (used when echoing
# canceled or submitted input back to scrollback via plain print).
PILL_PT_STYLE = "fg:ansicyan bold"
_PILL_ANSI_OPEN = fg("cyan") + attr("bold")


def ansi_pill(text: str) -> str:
    """Wrap text in the pill (cyan bold) ANSI style."""
    return f"{_PILL_ANSI_OPEN}{text}{RESET_ANSI}"


USER_LABEL = LabelStyle(
    "User: ",
    fg("green") + attr("bold"),
    "green bold",
    "ansigreen",
)
AI_LABEL = LabelStyle("AI: ", fg("blue") + attr("bold"), "blue bold", "ansiblue")
SYSTEM_LABEL = LabelStyle(
    "System: ",
    fg("violet") + attr("bold"),
    "magenta bold",
    "ansimagenta",
)
INFO_LABEL = LabelStyle("Info: ", fg("cyan") + attr("bold"), "cyan bold")
WARNING_LABEL = LabelStyle(
    "Warning: ",
    fg("yellow") + attr("bold"),
    "yellow bold",
)
ERROR_LABEL = LabelStyle("Error: ", fg("red") + attr("bold"), "red bold")


def ansi_label(label: LabelStyle, text: str | None = None) -> str:
    """Return a colored ANSI label."""
    return f"{label.ansi_style}{text or label.text}{RESET_ANSI}"


def ansi_message(
    label: LabelStyle, message: str, *, label_text: str | None = None
) -> str:
    """Return an ANSI-colored message prefixed by the label."""
    return f"{ansi_label(label, text=label_text)}{message}"


def rich_label(label: LabelStyle, text: str | None = None) -> Text:
    """Return a Rich text label."""
    return Text(text or label.text, style=label.rich_style)


def rich_message(label: LabelStyle, message: str, *, dim: bool = False) -> Text:
    """Return a Rich text message with a styled label prefix."""
    text = rich_label(label)
    text.append(message, style="dim" if dim else None)
    return text


def prompt_html_label(label: LabelStyle, text: str | None = None) -> HTML:
    """Return a prompt_toolkit HTML label."""
    if label.prompt_html_color is None:
        raise ValueError(f"No prompt HTML color configured for {label.text!r}")

    label_text = text or label.text
    return HTML(
        f"<{label.prompt_html_color}><b>{label_text}</b></{label.prompt_html_color}>"
    )

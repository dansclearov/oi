"""Plaintext + styled transcript views shared by the chat selector.

All three consumers (preview pane, $EDITOR export, body search) build on
`flatten_history` so they see the same role+text view the main app renders.
"""

from rich.text import Text

from oi.core.message_utils import flatten_history
from oi.core.session import Chat
from oi.ui.labels import AI_LABEL, USER_LABEL, LabelStyle

_ROLE_LABELS: dict[str, LabelStyle] = {"user": USER_LABEL, "assistant": AI_LABEL}


def _role_header(role: str) -> str:
    label = _ROLE_LABELS.get(role)
    return label.text.strip() if label else f"{role}:"


def search_blob(title: str, chat: Chat) -> str:
    """Lowercased title + all message text, for substring search."""
    parts = [title]
    parts.extend(text for _role, text in flatten_history(chat.messages))
    return "\n".join(parts).lower()


def plaintext_transcript(chat: Chat) -> str:
    """Cleaned role + message text for opening in $EDITOR (no markup)."""
    lines = [
        f"**{_role_header(role)}** {text}"
        for role, text in flatten_history(chat.messages)
    ]
    return "\n".join(lines) + "\n"


def styled_transcript(chat: Chat) -> Text:
    """A Rich Text of the transcript with USER/AI labels, for the preview pane."""
    out = Text()
    for index, (role, text) in enumerate(flatten_history(chat.messages)):
        if index:
            out.append("\n")
        label = _ROLE_LABELS.get(role)
        if label:
            out.append(label.text, style=label.rich_style)
        else:
            out.append(f"{role}: ", style="bold")
        out.append(text)
    return out

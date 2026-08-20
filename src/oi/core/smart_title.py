"""Smart title generation helpers."""

from typing import Any, Optional, Sequence

from rich.cells import cell_len, set_cell_size

from oi.constants import MAX_TITLE_LENGTH
from oi.core.message_utils import build_prompt, flatten_history, response_text
from oi.core.session import Chat
from oi.llm_types import ChatOptions

TITLE_PROMPT_INSTRUCTIONS = (
    "Generate a concise 5-10 word title for this conversation. "
    "No quotes, no punctuation, just the title."
)
TITLE_SAMPLE_MESSAGES = 8


def truncate_to_cells(text: str, max_cells: int) -> str:
    """Cut ``text`` to ``max_cells`` terminal columns.

    Titles are budgeted against the chat selector's title column, which is
    measured in cells — so the cap has to be too, or a title of wide (CJK)
    characters stores at twice its budget. Cutting through a wide character
    leaves ``set_cell_size`` a padding space to strip."""
    if cell_len(text) <= max_cells:
        return text
    return set_cell_size(text, max_cells).rstrip()


class SmartTitleGenerator:
    """Generate concise chat titles from early conversation turns."""

    def generate(self, chat: Chat, llm_client: Any, model: str) -> Optional[str]:
        flattened_history = flatten_history(chat.messages)
        if not flattened_history:
            return None

        title_prompt = self._build_title_prompt(flattened_history)
        options = ChatOptions(
            enable_search=False,
            enable_thinking=False,
            show_thinking=False,
            silent=True,
        )
        response = llm_client.chat(title_prompt, model, options)
        new_title = self._sanitize_title(response_text(response))
        return new_title or None

    def _build_title_prompt(self, flattened_history: Sequence[tuple[str, str]]):
        conversation_sample = []
        for role, content in flattened_history[:TITLE_SAMPLE_MESSAGES]:
            prefix = "User" if role == "user" else "Assistant"
            conversation_sample.append(f"{prefix}: {content}")

        conversation_text = "\n".join(conversation_sample)
        return build_prompt(
            TITLE_PROMPT_INSTRUCTIONS,
            f"Conversation:\n{conversation_text}\n\nTitle:",
        )

    def _sanitize_title(self, title: str) -> str:
        cleaned = title.strip().strip("\"'").strip()
        if cell_len(cleaned) > MAX_TITLE_LENGTH:
            cleaned = truncate_to_cells(cleaned, MAX_TITLE_LENGTH - 1) + "…"
        return cleaned

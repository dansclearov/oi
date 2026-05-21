"""Helpers for local in-chat slash commands."""

from dataclasses import dataclass
from difflib import get_close_matches

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


@dataclass(frozen=True)
class LocalCommandSpec:
    name: str
    description: str


LOCAL_COMMAND_SPECS = (
    LocalCommandSpec("/bookmark", "Toggle bookmark for the current chat"),
    LocalCommandSpec("/vim", "Toggle vim input mode"),
)
LOCAL_COMMANDS = {spec.name: spec for spec in LOCAL_COMMAND_SPECS}
LOCAL_COMMAND_NAMES = tuple(spec.name for spec in LOCAL_COMMAND_SPECS)


def parse_local_command(text: str) -> tuple[str, str] | None:
    """Return the command name and trailing args for slash-prefixed input."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped.split(maxsplit=1)
    command_name = parts[0]
    command_args = parts[1] if len(parts) > 1 else ""
    return command_name, command_args


def build_unknown_command_message(command_name: str) -> str:
    """Return a user-facing message for an unknown slash command."""
    suggestion = get_close_matches(command_name, LOCAL_COMMAND_NAMES, n=1, cutoff=0.6)
    if suggestion:
        return f"Unknown command: {command_name}. Did you mean {suggestion[0]}?"

    available_commands = ", ".join(LOCAL_COMMAND_NAMES)
    return f"Unknown command: {command_name}. Available commands: {available_commands}"


def build_argument_error_message(command_name: str) -> str:
    """Return a user-facing message for unsupported command arguments."""
    return f"{command_name} does not take arguments."


def _get_completion_prefix(document: Document) -> str | None:
    """Return the slash-command prefix when completion should be active."""
    if "\n" in document.text:
        return None

    stripped_text = document.text.lstrip()
    if not stripped_text.startswith("/"):
        return None

    if any(char.isspace() for char in stripped_text):
        return None

    prefix = document.text_before_cursor.lstrip()
    if not prefix.startswith("/"):
        return None

    if any(char.isspace() for char in prefix):
        return None

    return prefix


class SlashCommandCompleter(Completer):
    """Complete standalone local slash commands."""

    def get_completions(self, document, complete_event):
        prefix = _get_completion_prefix(document)
        if prefix is None:
            return

        for spec in LOCAL_COMMAND_SPECS:
            if spec.name.startswith(prefix):
                yield Completion(
                    spec.name,
                    start_position=-len(prefix),
                    display_meta=spec.description,
                )

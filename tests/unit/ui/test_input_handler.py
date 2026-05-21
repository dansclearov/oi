from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.shortcuts import CompleteStyle

from oi.local_commands import SlashCommandCompleter
from oi.ui.input_handler import InputHandler


def _patch_session(return_value=None, side_effect=None):
    """Stub PromptSession so tests don't need a real terminal."""
    session_instance = MagicMock()
    if side_effect is not None:
        session_instance.prompt.side_effect = side_effect
    else:
        session_instance.prompt.return_value = return_value
    session_class = MagicMock(return_value=session_instance)
    return (
        patch("oi.ui.input_handler.PromptSession", session_class),
        session_class,
        session_instance,
    )


def test_get_user_input_preserves_whitespace():
    input_handler = InputHandler()

    patcher, _, _ = _patch_session(return_value="  padded message  ")
    with patcher:
        assert input_handler.get_user_input() == "  padded message  "


def test_get_user_input_maps_eof_to_keyboard_interrupt():
    input_handler = InputHandler()

    patcher, _, _ = _patch_session(side_effect=EOFError)
    with patcher:
        with pytest.raises(KeyboardInterrupt):
            input_handler.get_user_input()


def test_get_user_input_passes_slash_command_completer():
    input_handler = InputHandler()

    patcher, session_class, _ = _patch_session(return_value="/bookmark")
    with patcher:
        assert input_handler.get_user_input() == "/bookmark"

    kwargs = session_class.call_args.kwargs
    assert isinstance(kwargs["completer"], SlashCommandCompleter)
    assert kwargs["complete_style"] == CompleteStyle.READLINE_LIKE
    assert kwargs["erase_when_done"] is True


def test_slash_command_completer_suggests_known_commands_only():
    completer = SlashCommandCompleter()
    completions = list(
        completer.get_completions(
            Document("/bo"),
            CompleteEvent(completion_requested=True),
        )
    )

    assert [completion.text for completion in completions] == ["/bookmark"]

    non_command_completions = list(
        completer.get_completions(
            Document("plain message"),
            CompleteEvent(completion_requested=True),
        )
    )
    assert non_command_completions == []

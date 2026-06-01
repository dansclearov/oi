from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from pydantic_ai.messages import ModelResponse, TextPart

from oi.app import (
    ChatLoopContext,
    _handle_local_command,
    handle_chat_selection,
    run_chat_loop,
)
from oi.config.settings import Config
from oi.core.session import Chat, ChatMetadata
from oi.exceptions import ChatNotFoundError
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.ui.labels import WARNING_LABEL, ansi_message


def _make_ctx(**overrides: Any) -> ChatLoopContext:
    """Build a ChatLoopContext with mock defaults, overridable per-field."""
    llm_client = overrides.get("llm_client", Mock())
    # The startup billing indicator resolves the active model through the
    # registry; give the mock sane, non-subscription returns.
    llm_client.registry.get_provider_for_model.return_value = (
        "anthropic",
        "claude-sonnet",
    )
    llm_client.registry.get_model_capabilities.return_value = ModelCapabilities()
    return ChatLoopContext(
        config=overrides.get("config", Config()),
        chat_manager=overrides.get("chat_manager", Mock()),
        llm_client=llm_client,
        input_handler=overrides.get("input_handler", Mock()),
        chat_options=overrides.get("chat_options", ChatOptions()),
        prompt_str=overrides.get("prompt_str", "You are helpful."),
        active_model=overrides.get("active_model", "sonnet"),
    )


def test_run_chat_loop_skips_empty_input():
    metadata = ChatMetadata(
        id="test-chat",
        title="Chat 2026-02-14 00:00",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)

    llm_client = Mock()
    input_handler = Mock()
    input_handler.get_user_input.side_effect = ["", KeyboardInterrupt()]
    ctx = _make_ctx(llm_client=llm_client, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    llm_client.chat.assert_not_called()
    assert current_chat.messages == []


def test_run_chat_loop_skips_whitespace_only_input():
    metadata = ChatMetadata(
        id="test-chat-whitespace",
        title="Chat 2026-02-14 00:00",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)

    llm_client = Mock()
    input_handler = Mock()
    input_handler.get_user_input.side_effect = ["   ", KeyboardInterrupt()]
    ctx = _make_ctx(llm_client=llm_client, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    llm_client.chat.assert_not_called()
    assert current_chat.messages == []


def test_run_chat_loop_uses_active_model_for_resumed_chat():
    metadata = ChatMetadata(
        id="test-chat-resume",
        title="Existing chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=4,
    )
    current_chat = Chat(metadata=metadata)
    current_chat.metadata.set_model_capabilities_snapshot(
        ModelCapabilities(
            supports_search=True,
            supports_thinking=False,
            extra_params={"example": True},
        )
    )
    current_chat.append_user_message("Earlier user message")
    current_chat.append_assistant_response("Earlier assistant message")

    llm_client = Mock()
    llm_client.chat.return_value = ModelResponse(parts=[TextPart(content="new reply")])
    input_handler = Mock()
    input_handler.get_user_input.side_effect = ["Next question", KeyboardInterrupt()]
    ctx = _make_ctx(llm_client=llm_client, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    assert llm_client.chat.call_args[0][1] == "sonnet"
    capabilities_override = llm_client.chat.call_args.kwargs["capabilities_override"]
    assert capabilities_override is not None
    assert capabilities_override.supports_search is True
    assert capabilities_override.supports_thinking is False
    assert capabilities_override.extra_params == {"example": True}


def test_handle_chat_selection_exits_for_missing_explicit_resume():
    args = SimpleNamespace(resume="missing-id", **{"continue": False})
    chat_manager = Mock()
    chat_manager.load_chat.side_effect = ChatNotFoundError("Chat not found: missing-id")

    with pytest.raises(SystemExit) as exc_info:
        handle_chat_selection(args, chat_manager)

    assert cast(SystemExit, exc_info.value).code == 1


def test_run_chat_loop_discards_user_message_on_request_error():
    metadata = ChatMetadata(
        id="test-chat-error",
        title="Chat 2026-02-14 00:00",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)

    llm_client = Mock()
    llm_client.chat.side_effect = RuntimeError("upstream failed")
    input_handler = Mock()
    input_handler.get_user_input.side_effect = ["Hello", KeyboardInterrupt()]
    ctx = _make_ctx(llm_client=llm_client, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    # Failed requests should not leave orphan user messages behind.
    assert current_chat.messages == []


def test_run_chat_loop_warns_when_response_hits_output_limit(capsys):
    metadata = ChatMetadata(
        id="test-chat-length-stop",
        title="Chat 2026-02-14 00:00",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)

    llm_client = Mock()
    llm_client.chat.return_value = ModelResponse(
        parts=[TextPart(content="truncated")],
        finish_reason="length",
    )
    input_handler = Mock()
    input_handler.get_user_input.side_effect = ["Hello", KeyboardInterrupt()]
    ctx = _make_ctx(llm_client=llm_client, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    assert (
        ansi_message(
            WARNING_LABEL,
            "Response hit the model output limit. Set `max_tokens` for this model "
            "in models.yaml if you want longer replies.",
        )
        in capsys.readouterr().out
    )


def test_handle_local_command_toggles_bookmark_for_saved_chat():
    metadata = ChatMetadata(
        id="test-chat-bookmark",
        title="Existing chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=2,
    )
    current_chat = Chat(metadata=metadata)
    current_chat.append_user_message("Hello")
    current_chat.append_assistant_response("Hi there!")
    chat_manager = Mock()
    chat_manager.toggle_bookmark.return_value = True

    handled = _handle_local_command(
        "/bookmark",
        Config(),
        current_chat,
        chat_manager,
    )

    assert handled is True
    chat_manager.toggle_bookmark.assert_called_once_with(current_chat)


def test_handle_local_command_rejects_bookmark_for_unsaved_chat(capsys):
    metadata = ChatMetadata(
        id="test-chat-unsaved-bookmark",
        title="New chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)
    chat_manager = Mock()

    handled = _handle_local_command(
        "/bookmark",
        Config(),
        current_chat,
        chat_manager,
    )

    assert handled is True
    chat_manager.toggle_bookmark.assert_not_called()
    assert (
        ansi_message(
            WARNING_LABEL,
            "Bookmarking is available after the first saved exchange.",
        )
        in capsys.readouterr().out
    )


def test_handle_local_command_rejects_unknown_slash_command(capsys):
    metadata = ChatMetadata(
        id="test-chat-unknown-command",
        title="New chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)
    chat_manager = Mock()

    handled = _handle_local_command(
        "/bookamrk",
        Config(),
        current_chat,
        chat_manager,
    )

    assert handled is True
    chat_manager.toggle_bookmark.assert_not_called()
    assert (
        ansi_message(
            WARNING_LABEL,
            "Unknown command: /bookamrk. Did you mean /bookmark?",
        )
        in capsys.readouterr().out
    )


def _existing_chat() -> Chat:
    metadata = ChatMetadata(
        id="test-chat-exit",
        title="Existing chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=2,
    )
    current_chat = Chat(metadata=metadata)
    current_chat.append_user_message("Earlier user message")
    current_chat.append_assistant_response("Earlier assistant message")
    return current_chat


def test_run_chat_loop_touches_chat_on_exit():
    # Re-reading a chat and exiting with Ctrl+C should re-save it so its
    # updated_at bumps and `oi -c` reopens the one you just closed.
    current_chat = _existing_chat()

    chat_manager = Mock()
    input_handler = Mock()
    input_handler.get_user_input.side_effect = [KeyboardInterrupt()]
    ctx = _make_ctx(chat_manager=chat_manager, input_handler=input_handler)

    run_chat_loop(current_chat, ctx)

    chat_manager.save_chat.assert_called_once_with(current_chat)


def test_run_chat_loop_does_not_touch_chat_on_exit_when_ephemeral():
    current_chat = _existing_chat()

    chat_manager = Mock()
    input_handler = Mock()
    input_handler.get_user_input.side_effect = [KeyboardInterrupt()]
    ctx = _make_ctx(chat_manager=chat_manager, input_handler=input_handler)
    ctx.ephemeral = True

    run_chat_loop(current_chat, ctx)

    chat_manager.save_chat.assert_not_called()

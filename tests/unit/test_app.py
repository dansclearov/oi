from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from rich.cells import cell_len
from pydantic_ai.messages import ModelResponse, TextPart

from oi.app import (
    ChatLoopContext,
    _handle_local_command,
    _update_title_from_first_user_message,
    handle_chat_selection,
    run_chat_loop,
)
from oi.config.settings import Config, load_user_config
from oi.constants import MAX_TITLE_LENGTH
from oi.core.session import Chat, ChatMetadata
from oi.exceptions import ChatNotFoundError
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.ui.labels import BTW_AI_LABEL_TEXT, WARNING_LABEL, ansi_message


_SEARCH_CAPS = ModelCapabilities(supports_search=True)


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
        _make_ctx(chat_manager=chat_manager),
        current_chat,
        _SEARCH_CAPS,
    )

    assert handled is True
    chat_manager.toggle_bookmark.assert_called_once_with(current_chat)


def test_search_command_turns_search_on_for_the_session(capsys):
    metadata = ChatMetadata(
        id="test-chat-search",
        title="Search",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    ctx = _make_ctx(chat_options=ChatOptions(enable_search=False))
    chat = Chat(metadata=metadata)

    assert _handle_local_command("/search", ctx, chat, _SEARCH_CAPS) is True
    assert ctx.chat_options.enable_search is True
    assert "Web search enabled" in capsys.readouterr().out

    # One-way: a second `/search` says so instead of turning it back off.
    assert _handle_local_command("/search", ctx, chat, _SEARCH_CAPS) is True
    assert ctx.chat_options.enable_search is True
    assert "already on" in capsys.readouterr().out


def test_search_command_reports_models_without_the_capability(capsys):
    metadata = ChatMetadata(
        id="test-chat-no-search",
        title="No search",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="local-model",
        message_count=0,
    )
    ctx = _make_ctx(chat_options=ChatOptions(enable_search=False))

    handled = _handle_local_command(
        "/search", ctx, Chat(metadata=metadata), ModelCapabilities()
    )

    assert handled is True
    assert ctx.chat_options.enable_search is False
    assert "local-model does not support web search" in capsys.readouterr().out


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
        _make_ctx(chat_manager=chat_manager),
        current_chat,
        _SEARCH_CAPS,
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
        _make_ctx(chat_manager=chat_manager),
        current_chat,
        _SEARCH_CAPS,
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


def test_vim_toggle_reports_a_config_that_could_not_be_saved(capsys, monkeypatch):
    metadata = ChatMetadata(
        id="test-chat-vim",
        title="New chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    monkeypatch.setattr(
        "oi.app.update_user_config",
        Mock(side_effect=OSError("Read-only file system")),
    )
    ctx = _make_ctx(config=Config(vim_mode=False))

    handled = _handle_local_command("/vim", ctx, Chat(metadata=metadata), _SEARCH_CAPS)

    assert handled is True
    # The session still gets vim mode; only the persistence is reported failed.
    assert ctx.config.vim_mode is True
    out = capsys.readouterr().out
    assert "Vim mode enabled for this session" in out
    assert "Read-only file system" in out


def test_tui_toggle_persists_the_setting(capsys):
    metadata = ChatMetadata(
        id="test-chat-tui",
        title="New chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    ctx = _make_ctx(config=Config(tui=False))

    handled = _handle_local_command("/tui", ctx, Chat(metadata=metadata), _SEARCH_CAPS)

    assert handled is True
    assert ctx.config.tui is True
    assert load_user_config()["tui"] is True
    assert "TUI mode enabled" in capsys.readouterr().out


def test_btw_runs_side_question_without_mutating_history():
    metadata = ChatMetadata(
        id="test-chat-btw",
        title="Existing chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=2,
    )
    current_chat = Chat(metadata=metadata)
    current_chat.append_user_message("Hello")
    current_chat.append_assistant_response("Hi there!")
    history_before = list(current_chat.messages)

    llm_client = Mock()
    ctx = _make_ctx(llm_client=llm_client)

    handled = _handle_local_command(
        "/btw what did I ask?", ctx, current_chat, _SEARCH_CAPS
    )

    assert handled is True
    # The chat is never mutated: question and answer are not persisted.
    assert current_chat.messages == history_before

    # chat() saw the full history plus the side question as a trailing turn.
    llm_client.chat.assert_called_once()
    side_messages = llm_client.chat.call_args.args[0]
    assert side_messages[: len(history_before)] == history_before
    assert len(side_messages) == len(history_before) + 1

    # The answer renders under the ephemeral "AI (btw): " label.
    options = llm_client.chat.call_args.args[2]
    assert options.assistant_label_text == BTW_AI_LABEL_TEXT


def test_btw_without_question_prints_usage_and_skips_request():
    metadata = ChatMetadata(
        id="test-chat-btw-empty",
        title="Existing chat",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    current_chat = Chat(metadata=metadata)
    llm_client = Mock()

    handled = _handle_local_command(
        "/btw", _make_ctx(llm_client=llm_client), current_chat, _SEARCH_CAPS
    )

    assert handled is True
    llm_client.chat.assert_not_called()


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


def test_first_message_title_is_capped_in_cells_and_marks_the_cut():
    """Wide characters cost two columns, so the cap has to count columns —
    otherwise a CJK title is stored at twice the chat selector's budget."""
    chat = Chat(
        metadata=ChatMetadata(
            id="c",
            title="Chat 2026-08-20 10:00",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            model="sonnet",
            message_count=0,
        )
    )
    chat.append_user_message("漢字" * MAX_TITLE_LENGTH)

    _update_title_from_first_user_message(chat)

    assert cell_len(chat.metadata.title) <= MAX_TITLE_LENGTH
    assert chat.metadata.title.endswith("…")

from datetime import datetime
from unittest.mock import Mock

from pydantic_ai.messages import ModelResponse, TextPart

from oi.constants import MAX_TITLE_LENGTH
from oi.core.message_utils import flatten_history
from oi.core.session import Chat, ChatMetadata
from oi.core.smart_title import SmartTitleGenerator


def _make_chat() -> Chat:
    metadata = ChatMetadata(
        id="chat-1",
        title="Chat 2026-02-24 10:00",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        model="sonnet",
        message_count=0,
    )
    return Chat(metadata=metadata)


def test_generate_returns_none_for_empty_chat():
    generator = SmartTitleGenerator()
    llm_client = Mock()
    chat = _make_chat()

    result = generator.generate(chat, llm_client, "sonnet")

    assert result is None
    llm_client.chat.assert_not_called()


def test_generate_builds_prompt_and_sanitizes_title():
    generator = SmartTitleGenerator()
    llm_client = Mock()
    llm_client.chat.return_value = ModelResponse(
        parts=[TextPart(content='"' + ("x" * (MAX_TITLE_LENGTH + 10)) + '"')]
    )
    chat = _make_chat()

    for i in range(5):
        chat.append_user_message(f"Question {i}")
        chat.append_assistant_response(f"Answer {i}")

    title = generator.generate(chat, llm_client, "sonnet")

    assert title is not None
    assert len(title) == MAX_TITLE_LENGTH
    assert title.endswith("...")

    prompt_messages, model, options = llm_client.chat.call_args[0]
    assert model == "sonnet"
    assert options.silent is True
    assert options.enable_search is False
    assert options.enable_thinking is False
    assert options.show_thinking is False

    prompt_history = flatten_history(prompt_messages)
    assert len(prompt_history) == 1
    prompt_text = prompt_history[0][1]
    assert "Question 3" in prompt_text
    assert "Answer 3" in prompt_text
    assert "Question 4" not in prompt_text
    assert "Answer 4" not in prompt_text

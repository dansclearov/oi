"""Helpers for working with pydantic-ai chat messages."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)


def serialize_model_messages(messages: Sequence[ModelMessage]) -> list[dict]:
    """Serialize model messages to a JSON-friendly structure."""
    if not messages:
        return []
    json_bytes = ModelMessagesTypeAdapter.dump_json(list(messages))
    return json.loads(json_bytes)


def deserialize_model_messages(data: Sequence[dict]) -> list[ModelMessage]:
    """Deserialize JSON data into model messages."""
    if not data:
        return []
    return ModelMessagesTypeAdapter.validate_python(list(data))


def convert_legacy_messages(
    legacy_messages: Sequence[dict[str, str]],
) -> list[ModelMessage]:
    """Convert legacy OpenAI-style dict messages into ModelMessage objects."""
    result: list[ModelMessage] = []
    pending_system_prompt: Optional[str] = None

    for message in legacy_messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "system":
            pending_system_prompt = content
            continue

        if role == "user":
            parts = []
            if pending_system_prompt is not None:
                parts.append(SystemPromptPart(pending_system_prompt))
                pending_system_prompt = None
            parts.append(UserPromptPart(content))
            result.append(ModelRequest(parts=parts))
        elif role == "assistant":
            parts = []
            if content:
                parts.append(TextPart(content=content))
            result.append(ModelResponse(parts=parts))

    return result


def flatten_history(messages: Sequence[ModelMessage]) -> list[tuple[str, str]]:
    """Flatten ModelMessages into (role, content) pairs for UI use."""
    history: list[tuple[str, str]] = []

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and part.content:
                    # Handle both str and Sequence[UserContent] types
                    content_str = (
                        part.content
                        if isinstance(part.content, str)
                        else str(part.content)
                    )
                    history.append(("user", content_str))
        elif isinstance(message, ModelResponse):
            text = "".join(
                part.content
                for part in message.parts
                if isinstance(part, TextPart) and part.content
            )
            if text:
                history.append(("assistant", text))

    return history


def latest_system_prompt(messages: Sequence[ModelMessage]) -> Optional[str]:
    """Return the last seen system prompt in the conversation, if any."""
    last_prompt: Optional[str] = None

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    last_prompt = part.content

    return last_prompt


def count_non_system_messages(messages: Sequence[ModelMessage]) -> int:
    """Count messages that should appear in the chat transcript (excludes system)."""
    count = 0
    for message in messages:
        if isinstance(message, ModelRequest):
            if any(isinstance(part, UserPromptPart) for part in message.parts):
                count += 1
        elif isinstance(message, ModelResponse):
            if any(
                isinstance(part, TextPart) and part.content for part in message.parts
            ):
                count += 1
    return count


def build_prompt(system_prompt: Optional[str], user_prompt: str) -> list[ModelMessage]:
    """Build a single-turn prompt with optional system instructions."""
    parts = []
    if system_prompt:
        parts.append(SystemPromptPart(system_prompt))
    parts.append(UserPromptPart(user_prompt))
    return [ModelRequest(parts=parts)]


def response_text(response: ModelResponse) -> str:
    """Extract concatenated text parts from a ModelResponse."""
    return "".join(
        part.content for part in response.parts if isinstance(part, TextPart)
    )

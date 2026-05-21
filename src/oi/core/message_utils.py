"""Helpers for working with pydantic-ai chat messages."""

from __future__ import annotations

import json
from typing import Callable, Optional, Sequence

from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
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


def render_user_prompt_content(
    content: object,
    *,
    image_wrap: Optional[Callable[[str], str]] = None,
) -> str:
    """Render a UserPromptPart.content payload as a display string.

    `image_wrap` lets UI callers style the `[Image #N]` placeholder
    (e.g. ANSI pill styling) without coupling this module to terminal output.
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, (list, tuple)):
        return str(content)

    rendered: list[str] = []
    image_index = 0
    for part in content:
        if isinstance(part, str):
            rendered.append(part)
        elif isinstance(part, (BinaryContent, ImageUrl)):
            image_index += 1
            placeholder = f"[Image #{image_index}] "
            rendered.append(image_wrap(placeholder) if image_wrap else placeholder)
        else:
            rendered.append(str(part))
    return "".join(rendered)


def flatten_history(
    messages: Sequence[ModelMessage],
    *,
    image_wrap: Optional[Callable[[str], str]] = None,
) -> list[tuple[str, str]]:
    """Flatten ModelMessages into (role, content) pairs for UI use.

    `image_wrap` is forwarded to `render_user_prompt_content` so display
    callers can style image placeholders. Title-generation callers should
    leave it None to keep output plain.
    """
    history: list[tuple[str, str]] = []

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and part.content:
                    history.append(
                        (
                            "user",
                            render_user_prompt_content(
                                part.content, image_wrap=image_wrap
                            ),
                        )
                    )
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

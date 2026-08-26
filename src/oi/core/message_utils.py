"""Helpers for working with pydantic-ai chat messages."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterable, Optional, Sequence

# pydantic_ai imports are function-local: its package __init__ costs ~600ms,
# which would otherwise land on every startup before the first prompt paints.
if TYPE_CHECKING:
    from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse


# A completed ATX heading at the very end of a text block. `\Z` (not `$`) so a
# block that ends in a newline never matches — nothing was dropped there.
_TRAILING_HEADING = re.compile(r"(?:^|\n)[ \t]{0,3}#{1,6}(?:[ \t][^\n]*)?\Z")


def text_part_separator(accumulated: str, following: str) -> str:
    """Return the break to restore between two assistant text blocks.

    Anthropic splits a cited answer into one text block per citation and strips
    each block's trailing newlines, so a heading ending one block arrives glued
    to the sentence opening the next: `## SpecsBoth models have ...`, rendered
    as one long heading. A heading is line-terminated by definition, so a block
    that follows one without starting on a new line lost that break.
    """
    if not accumulated or not following:
        return ""
    if accumulated.endswith("\n") or following.startswith("\n"):
        return ""
    return "\n\n" if _TRAILING_HEADING.search(accumulated) else ""


def join_text_parts(contents: Iterable[str]) -> str:
    """Concatenate a response's text blocks, restoring dropped line breaks."""
    joined = ""
    for content in contents:
        if not content:
            continue
        joined += text_part_separator(joined, content) + content
    return joined


def prune_interrupted_response(response: ModelResponse) -> Optional[ModelResponse]:
    """Reduce an interrupted response to the parts safe to keep in history.

    A stream cut mid-turn can end on a native tool call whose result never
    arrived; a dangling call replays badly (providers either reject it or read
    it as a search that ran and returned nothing), so it is dropped. Partial
    text and thinking replay fine — pydantic-ai skips empty blocks and wraps
    unsigned thinking as tagged text. Returns None when nothing with content
    survives, i.e. there is no assistant message worth storing.
    """
    from dataclasses import replace

    from pydantic_ai.messages import (
        NativeToolCallPart,
        NativeToolReturnPart,
        TextPart,
        ThinkingPart,
    )

    answered = {
        part.tool_call_id
        for part in response.parts
        if isinstance(part, NativeToolReturnPart)
    }
    parts = [
        part
        for part in response.parts
        if not (
            isinstance(part, NativeToolCallPart) and part.tool_call_id not in answered
        )
    ]

    def has_substance(part) -> bool:
        if isinstance(part, (TextPart, ThinkingPart)):
            return bool(part.content.strip())
        return True

    if not any(has_substance(part) for part in parts):
        return None
    return replace(response, parts=parts, state="interrupted")


def serialize_model_messages(messages: Sequence[ModelMessage]) -> list[dict]:
    """Serialize model messages to a JSON-friendly structure."""
    if not messages:
        return []
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    json_bytes = ModelMessagesTypeAdapter.dump_json(list(messages))
    return json.loads(json_bytes)


def deserialize_model_messages(data: Sequence[dict]) -> list[ModelMessage]:
    """Deserialize JSON data into model messages."""
    if not data:
        return []
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.validate_python(list(data))


def convert_legacy_messages(
    legacy_messages: Sequence[dict[str, str]],
) -> list[ModelMessage]:
    """Convert legacy OpenAI-style dict messages into ModelMessage objects."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        UserPromptPart,
    )

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

    from pydantic_ai.messages import BinaryContent, ImageUrl

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
    if not messages:
        return history

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

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
            text = join_text_parts(
                part.content for part in message.parts if isinstance(part, TextPart)
            )
            if text:
                history.append(("assistant", text))

    return history


def user_message_indices(messages: Sequence[ModelMessage]) -> list[int]:
    """Indices of the messages `flatten_history` shows as user turns."""
    indices: list[int] = []
    if not messages:
        return indices

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    for index, message in enumerate(messages):
        if isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) and part.content for part in message.parts
        ):
            indices.append(index)
    return indices


def user_timestamp(message: ModelMessage) -> datetime:
    """When the user prompt in `message` was written."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    assert isinstance(message, ModelRequest)
    for part in message.parts:
        if isinstance(part, UserPromptPart):
            return part.timestamp
    raise ValueError("message carries no user prompt")


def split_user_content(
    content: object,
) -> tuple[str, dict[int, BinaryContent]]:
    """Take a user prompt apart into editable text plus its images.

    The inverse of the TUI's submit: images become `[Image #N] ` markers in
    the text, keyed by N in the returned map, so the message can be loaded
    back into the input with its pills live.
    """
    if isinstance(content, str):
        return content, {}

    from pydantic_ai.messages import BinaryContent

    text = ""
    images: dict[int, BinaryContent] = {}
    for part in content:  # type: ignore[attr-defined]
        if isinstance(part, BinaryContent):
            images[len(images) + 1] = part
            text += f"[Image #{len(images)}] "
        else:
            text += str(part)
    return text, images


def latest_system_prompt(messages: Sequence[ModelMessage]) -> Optional[str]:
    """Return the last seen system prompt in the conversation, if any."""
    last_prompt: Optional[str] = None
    if not messages:
        return last_prompt

    from pydantic_ai.messages import ModelRequest, SystemPromptPart

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    last_prompt = part.content

    return last_prompt


def count_non_system_messages(messages: Sequence[ModelMessage]) -> int:
    """Count messages that should appear in the chat transcript (excludes system)."""
    count = 0
    if not messages:
        return count

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

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
    from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

    parts = []
    if system_prompt:
        parts.append(SystemPromptPart(system_prompt))
    parts.append(UserPromptPart(user_prompt))
    return [ModelRequest(parts=parts)]


def response_text(response: ModelResponse) -> str:
    """Extract concatenated text parts from a ModelResponse."""
    from pydantic_ai.messages import TextPart

    return join_text_parts(
        part.content for part in response.parts if isinstance(part, TextPart)
    )

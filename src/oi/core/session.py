"""Chat session management."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from collections.abc import Sequence

# pydantic_ai imports are function-local: its package __init__ costs ~600ms,
# which would otherwise land on every startup before the first prompt paints.
if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelResponse, UserContent

from oi.core.message_utils import (
    count_non_system_messages,
)
from oi.llm_types import ModelCapabilities


@dataclass
class ChatMetadata:
    """Metadata for a chat session."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    model: str
    message_count: int
    bookmarked: bool = False
    smart_title_generated: bool = False
    model_capabilities_snapshot: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "model": self.model,
            "message_count": self.message_count,
            "bookmarked": self.bookmarked,
            "smart_title_generated": self.smart_title_generated,
        }
        if self.model_capabilities_snapshot is not None:
            data["model_capabilities_snapshot"] = copy.deepcopy(
                self.model_capabilities_snapshot
            )
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ChatMetadata":
        raw_snapshot = data.get("model_capabilities_snapshot")
        snapshot = (
            copy.deepcopy(raw_snapshot) if isinstance(raw_snapshot, dict) else None
        )
        return cls(
            id=data["id"],
            title=data["title"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            model=data["model"],
            message_count=data["message_count"],
            bookmarked=data.get("bookmarked", False),
            smart_title_generated=data.get("smart_title_generated", False),
            model_capabilities_snapshot=snapshot,
        )

    def set_model_capabilities_snapshot(self, capabilities: ModelCapabilities) -> None:
        """Persist a JSON-safe snapshot of model capabilities for this chat."""
        self.model_capabilities_snapshot = {
            "supports_search": bool(capabilities.supports_search),
            "supports_thinking": bool(capabilities.supports_thinking),
            "supports_vision": bool(capabilities.supports_vision),
            "max_tokens": capabilities.max_tokens,
            "extra_params": copy.deepcopy(capabilities.extra_params),
        }

    def get_model_capabilities_snapshot(self) -> ModelCapabilities | None:
        """Return a typed capabilities snapshot when one is available."""
        raw = self.model_capabilities_snapshot
        if not isinstance(raw, dict):
            return None

        extra_params = raw.get("extra_params", {})
        safe_extra_params = (
            copy.deepcopy(extra_params) if isinstance(extra_params, dict) else {}
        )
        return ModelCapabilities(
            supports_search=bool(raw.get("supports_search", False)),
            supports_thinking=bool(raw.get("supports_thinking", False)),
            supports_vision=bool(raw.get("supports_vision", False)),
            max_tokens=raw.get("max_tokens"),
            extra_params=safe_extra_params,
        )


@dataclass
class Chat:
    """A chat session with messages and metadata."""

    metadata: ChatMetadata
    messages: list[ModelMessage] = field(default_factory=list)
    pending_system_prompt: Optional[str] = None

    @classmethod
    def create_new(cls, model: str, system_message: str) -> "Chat":
        """Create a new unsaved chat with a placeholder title."""
        now = datetime.now()
        chat_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"

        metadata = ChatMetadata(
            id=chat_id,
            title=f"Chat {now.strftime('%Y-%m-%d %H:%M')}",
            created_at=now,
            updated_at=now,
            model=model,
            message_count=0,
        )
        return cls(metadata=metadata, pending_system_prompt=system_message)

    def append_user_message(self, content: str | Sequence[UserContent]) -> None:
        """Append a user message, injecting system prompt if pending."""
        from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

        parts = []
        if self.pending_system_prompt:
            parts.append(SystemPromptPart(self.pending_system_prompt))
            self.pending_system_prompt = None
        parts.append(UserPromptPart(content))
        self.messages.append(ModelRequest(parts=parts))

    def append_assistant_response(
        self, response: ModelResponse | str, *, allow_empty: bool = False
    ) -> None:
        """Append an assistant response."""
        from pydantic_ai.messages import ModelResponse, TextPart

        if isinstance(response, ModelResponse):
            self.messages.append(response)
            return

        if not response and not allow_empty:
            return

        parts = []
        if response:
            parts.append(TextPart(content=response))
        self.messages.append(ModelResponse(parts=parts))

    def discard_pending_user_message(self) -> None:
        """Drop a trailing user request after a failed or interrupted turn.

        If the request carried the system prompt (first turn of a new chat),
        restore it as pending so the next message re-injects it.
        """
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        if not self.messages or not isinstance(self.messages[-1], ModelRequest):
            return

        request = self.messages.pop()
        for part in request.parts:
            if isinstance(part, SystemPromptPart):
                self.pending_system_prompt = part.content
                break

    def should_be_saved(self) -> bool:
        """Check if chat should be saved (has non-system messages)."""
        return count_non_system_messages(self.messages) > 0

"""Chat session management."""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserContent,
    UserPromptPart,
)

from llm_cli.core.message_utils import (
    count_non_system_messages,
)
from llm_cli.llm_types import ModelCapabilities


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
        if isinstance(response, ModelResponse):
            self.messages.append(response)
            return

        if not response and not allow_empty:
            return

        parts = []
        if response:
            parts.append(TextPart(content=response))
        self.messages.append(ModelResponse(parts=parts))

    def should_be_saved(self) -> bool:
        """Check if chat should be saved (has non-system messages)."""
        return count_non_system_messages(self.messages) > 0

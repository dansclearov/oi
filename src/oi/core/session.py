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
    deserialize_model_messages,
    serialize_model_messages,
    user_timestamp,
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
class Branch:
    """A dormant alternative to the active path from message index `at` on.

    `tail` replaces `messages[at:]`; it starts with a user request. A branch
    carries its own dormant sub-branches, whose `at` indexes the path this
    branch would form once active (`messages[:at] + tail`), so the whole tree
    is expressed without message identifiers.
    """

    at: int
    tail: list[ModelMessage]
    branches: list["Branch"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "tail": serialize_model_messages(self.tail),
            "branches": [branch.to_dict() for branch in self.branches],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Branch":
        return cls(
            at=int(data["at"]),
            tail=deserialize_model_messages(data["tail"]),
            branches=[cls.from_dict(item) for item in data.get("branches", [])],
        )

    @property
    def timestamp(self) -> datetime:
        return user_timestamp(self.tail[0])


@dataclass
class Chat:
    """A chat session with messages and metadata.

    `messages` is the active path through the conversation tree; `branches`
    holds the dormant alternatives (see `Branch`). Everything that reads a
    chat — the model, replay, search, stats — sees only the active path.
    """

    metadata: ChatMetadata
    messages: list[ModelMessage] = field(default_factory=list)
    pending_system_prompt: Optional[str] = None
    branches: list[Branch] = field(default_factory=list)

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
        from pydantic_ai.messages import ModelRequest

        if not self.messages or not isinstance(self.messages[-1], ModelRequest):
            return

        self._restore_system_prompt(self.messages.pop())

    # ------------------------------------------------------------ branches

    def siblings_at(self, index: int) -> list[Optional[Branch]]:
        """The alternatives at `index`, oldest first; None is the active one."""
        siblings: list[Optional[Branch]] = [
            branch for branch in self.branches if branch.at == index
        ]
        siblings.append(None)
        active_stamp = user_timestamp(self.messages[index])
        siblings.sort(
            key=lambda branch: active_stamp if branch is None else branch.timestamp
        )
        return siblings

    def sibling_position(self, index: int) -> tuple[int, int]:
        """1-based position of the active path among the siblings at `index`."""
        siblings = self.siblings_at(index)
        return siblings.index(None) + 1, len(siblings)

    def fork_at(self, index: int, content: str | Sequence[UserContent]) -> Branch:
        """Replace the user message at `index` and everything after it.

        The displaced tail becomes a dormant branch (returned, so a turn that
        never produces anything can put it back with `switch_to`), taking with
        it the dormant branches that hang off that tail. A system prompt on
        the displaced message carries over to the new one.
        """
        tail = self.messages[index:]
        nested = [branch for branch in self.branches if branch.at > index]
        self.branches = [branch for branch in self.branches if branch.at <= index]
        displaced = Branch(at=index, tail=tail, branches=nested)
        self.branches.append(displaced)
        del self.messages[index:]
        self._restore_system_prompt(tail[0])
        self.append_user_message(content)
        return displaced

    def switch_to(self, branch: Branch) -> None:
        """Make a dormant branch the active path, parking the current tail."""
        index = branch.at
        tail = self.messages[index:]
        kept = [
            other
            for other in self.branches
            if other is not branch and other.at <= index
        ]
        if tail:
            nested = [other for other in self.branches if other.at > index]
            kept.append(Branch(at=index, tail=tail, branches=nested))
        self.branches = kept + branch.branches
        self.messages[index:] = branch.tail
        if index == 0:
            # The branch's first message carries the system prompt, if any.
            self.pending_system_prompt = None

    def _restore_system_prompt(self, request: ModelMessage) -> None:
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        if not isinstance(request, ModelRequest):
            return
        for part in request.parts:
            if isinstance(part, SystemPromptPart):
                self.pending_system_prompt = part.content
                return

    def should_be_saved(self) -> bool:
        """Check if chat should be saved (has non-system messages)."""
        return count_non_system_messages(self.messages) > 0

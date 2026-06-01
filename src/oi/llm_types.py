"""Shared LLM-related data structures."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelCapabilities:
    """Capabilities of a specific model."""

    supports_search: bool = False
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_subscription: bool = False
    max_tokens: int | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatOptions:
    """Options for chat requests."""

    enable_search: bool = False
    enable_thinking: bool = True
    show_thinking: bool = True
    show_assistant_label: bool = True
    silent: bool = False  # Suppress all console output

    extra_settings: dict[str, Any] = field(default_factory=dict)
    """Provider-specific overrides that can be attached to a request."""

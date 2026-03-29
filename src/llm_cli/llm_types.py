"""Shared LLM-related data structures."""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ModelCapabilities:
    """Capabilities of a specific model."""

    supports_search: bool = False
    supports_thinking: bool = False
    max_tokens: int | None = None
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatOptions:
    """Options for chat requests."""

    enable_search: bool = False
    enable_thinking: bool = True
    show_thinking: bool = True
    silent: bool = False  # Suppress all console output

    extra_settings: Dict[str, Any] = field(default_factory=dict)
    """Provider-specific overrides that can be attached to a request."""

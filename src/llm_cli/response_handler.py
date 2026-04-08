from __future__ import annotations

import json
from typing import Optional, Sequence

from pydantic_ai.messages import (
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    FilePart,
    FinalResultEvent,
    ModelResponse,
    ModelResponsePart,
    ModelResponseStreamEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from llm_cli.constants import USE_STYLED_RENDERER
from llm_cli.llm_types import ChatOptions, ModelCapabilities
from llm_cli.renderers import PlainTextRenderer, ResponseRenderer, StyledRenderer


class ResponseHandler:
    """Handles streaming responses from different providers uniformly."""

    def __init__(
        self,
        capabilities: ModelCapabilities,
        options: ChatOptions,
    ):
        self.capabilities = capabilities
        self.options = options

        if USE_STYLED_RENDERER:
            self.renderer: ResponseRenderer = StyledRenderer(capabilities, options)
        else:
            self.renderer = PlainTextRenderer(capabilities, options)

    def start_response(self) -> None:
        """Initialize the response rendering."""
        self.renderer.start_response()

    def handle_event(self, event: ModelResponseStreamEvent) -> None:
        """Handle a streaming event emitted by pydantic-ai."""
        if isinstance(event, PartStartEvent):
            self._handle_part(event.part)
        elif isinstance(event, PartDeltaEvent):
            self._handle_delta(event.delta)
        elif isinstance(event, PartEndEvent):
            self._handle_part_end(event.part)
        elif isinstance(event, FinalResultEvent):
            # No-op for now
            return

    def finish_response(self, response: Optional[ModelResponse] = None) -> None:
        """Finalize the response rendering."""
        if response and not self.renderer.get_full_response():
            fallback_text = self._extract_text(response.parts)
            if fallback_text:
                self.renderer.record_text(fallback_text)

        self.renderer.finish_response()

    def get_full_response(self) -> str:
        """Get the complete response content."""
        return self.renderer.get_full_response()

    def mark_interrupted(self) -> None:
        """Mark the response as interrupted by user."""
        self.renderer.mark_interrupted()

    def has_visible_output(self) -> bool:
        """Return whether any streamed output has already been emitted."""
        return self.renderer.has_visible_output()

    def _handle_part(self, part: ModelResponsePart) -> None:
        if isinstance(part, TextPart):
            self.renderer.render_text(part.content)
        elif isinstance(part, ThinkingPart):
            self.renderer.render_thinking(part.content)
        elif isinstance(part, BuiltinToolCallPart | BuiltinToolReturnPart):
            return  # Suppress built-in tool chatter (web_search, etc.)
        elif isinstance(part, ToolCallPart):
            if self._should_suppress_tool(part.tool_name):
                return
            description = self._format_tool(part.tool_name, part.args)
            if description:
                self.renderer.render_tool_call(description)
        elif isinstance(part, ToolReturnPart):
            if self._should_suppress_tool(part.tool_name):
                return
            description = self._format_tool(f"{part.tool_name} result", part.content)
            if description:
                self.renderer.render_tool_call(description)
        elif isinstance(part, FilePart):
            self.renderer.render_tool_call("[file attachment]")

    def _handle_delta(self, delta) -> None:
        if isinstance(delta, TextPartDelta):
            self.renderer.render_text(delta.content_delta)
        elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
            self.renderer.render_thinking(delta.content_delta)
        elif isinstance(delta, ToolCallPartDelta):
            if self._should_suppress_tool(delta.tool_name_delta):
                return
            description = self._format_tool(delta.tool_name_delta, delta.args_delta)
            if description:
                self.renderer.render_tool_call(description)

    def _handle_part_end(self, part: ModelResponsePart) -> None:
        if isinstance(part, ThinkingPart):
            self.renderer.close_thinking_section(final=True)

    def _format_tool(self, name: Optional[str], args) -> Optional[str]:
        """Create a basic human-readable tool description."""
        if not name:
            return None

        if args is None:
            return name

        if isinstance(args, dict):
            return f"{name} {json.dumps(args)}"

        return f"{name} {args}"

    def _should_suppress_tool(self, tool_name: Optional[str]) -> bool:
        if not tool_name:
            return False
        return tool_name in {
            "web_search",
            "code_execution",
            "url_context",
            "image_generation",
            "memory",
            "mcp_server",
        }

    def _extract_text(self, parts: Sequence[ModelResponsePart]) -> str:
        """Fallback text extraction when no stream deltas were emitted."""
        return "".join(
            part.content
            for part in parts
            if isinstance(part, TextPart) and part.content
        )

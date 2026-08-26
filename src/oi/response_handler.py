from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

# pydantic_ai imports are function-local: its package __init__ costs ~600ms,
# which would otherwise land on every startup before the first prompt paints.
if TYPE_CHECKING:
    from pydantic_ai.messages import (
        ModelResponse,
        ModelResponsePart,
        ModelResponseStreamEvent,
    )

from oi.core.message_utils import join_text_parts, text_part_separator
from oi.llm_types import ChatOptions, ModelCapabilities
from oi.renderers import ResponseRenderer, StyledRenderer


@dataclass
class _NativeCall:
    """A server-side tool call being assembled from stream events.

    Args stream separately from the call part on most providers: Anthropic
    sends JSON string fragments, OpenAI Responses one complete dict at
    `output_item.done`. The buffer accumulates string fragments until they
    parse as a whole.
    """

    call_id: str
    tool_name: str
    from_code: bool = False
    args_buffer: str = ""
    announced: bool = False
    announced_args: Optional[dict[str, Any]] = field(default=None)


class ResponseHandler:
    """Handles streaming responses from different providers uniformly."""

    def __init__(
        self,
        capabilities: ModelCapabilities,
        options: ChatOptions,
        renderer: Optional[ResponseRenderer] = None,
    ):
        self.capabilities = capabilities
        self.options = options

        self.renderer: ResponseRenderer = (
            renderer if renderer is not None else StyledRenderer(capabilities, options)
        )
        self._native_calls: dict[int, _NativeCall] = {}
        # Snapshot of an interrupted stream's accumulated response, set by the
        # client when the turn is cancelled mid-stream.
        self.partial_response: Optional[ModelResponse] = None

    def start_response(self) -> None:
        """Initialize the response rendering."""
        self.renderer.start_response()

    def handle_event(self, event: ModelResponseStreamEvent) -> None:
        """Handle a streaming event emitted by pydantic-ai."""
        from pydantic_ai.messages import (
            FinalResultEvent,
            PartDeltaEvent,
            PartEndEvent,
            PartStartEvent,
        )

        if isinstance(event, PartStartEvent):
            self._handle_part(event.part, event.index)
        elif isinstance(event, PartDeltaEvent):
            self._handle_delta(event.delta, event.index)
        elif isinstance(event, PartEndEvent):
            self._handle_part_end(event.part, event.index)
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

    def _handle_part(self, part: ModelResponsePart, index: int) -> None:
        from pydantic_ai.messages import (
            FilePart,
            NativeToolCallPart,
            NativeToolReturnPart,
            TextPart,
            ThinkingPart,
            ToolCallPart,
            ToolReturnPart,
        )

        if isinstance(part, TextPart):
            # Only at a part boundary: the separator keys off a trailing
            # heading, and a mid-part delta can end inside one ("## Sp").
            self.renderer.render_text(
                text_part_separator(self.renderer.get_full_response(), part.content)
            )
            self.renderer.render_text(part.content)
        elif isinstance(part, ThinkingPart):
            self.renderer.render_thinking(part.content)
        elif isinstance(part, NativeToolCallPart):
            call = _NativeCall(
                call_id=part.tool_call_id,
                tool_name=part.tool_name,
                # Anthropic stamps calls made from inside a code-execution
                # block with their caller; direct calls carry no marker.
                from_code="anthropic_caller" in (part.provider_details or {}),
            )
            self._native_calls[index] = call
            self._announce_native_call(call, coerce_tool_args(part.args))
        elif isinstance(part, NativeToolReturnPart):
            self.renderer.render_native_tool_return(
                part.tool_call_id, part.tool_name, part.content
            )
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

    def _handle_delta(self, delta, index: int) -> None:
        from pydantic_ai.messages import (
            TextPartDelta,
            ThinkingPartDelta,
            ToolCallPartDelta,
        )

        if isinstance(delta, TextPartDelta):
            self.renderer.render_text(delta.content_delta)
        elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
            self.renderer.render_thinking(delta.content_delta)
        elif isinstance(delta, ToolCallPartDelta):
            call = self._native_calls.get(index)
            if call is not None:
                self._apply_native_args_delta(call, delta.args_delta)
                return
            if self._should_suppress_tool(delta.tool_name_delta):
                return
            description = self._format_tool(delta.tool_name_delta, delta.args_delta)
            if description:
                self.renderer.render_tool_call(description)

    def _handle_part_end(self, part: ModelResponsePart, index: int) -> None:
        from pydantic_ai.messages import NativeToolCallPart, ThinkingPart

        if isinstance(part, ThinkingPart):
            self.renderer.close_thinking_section(final=True)
        elif isinstance(part, NativeToolCallPart):
            # The end event carries the authoritative final args; announce them
            # if the delta accumulation never produced a parseable dict.
            call = self._native_calls.pop(index, None)
            if call is not None:
                args = coerce_tool_args(part.args)
                if args is not None:
                    self._announce_native_call(call, args)

    def _announce_native_call(self, call: _NativeCall, args: Optional[dict]) -> None:
        if call.announced and (args is None or args == call.announced_args):
            return
        call.announced = True
        call.announced_args = args
        self.renderer.render_native_tool_call(
            call.call_id, call.tool_name, args, from_code=call.from_code
        )

    def _apply_native_args_delta(self, call: _NativeCall, args_delta) -> None:
        if isinstance(args_delta, dict):
            # OpenAI Responses delivers the complete args as one dict delta.
            self._announce_native_call(call, args_delta)
        elif isinstance(args_delta, str):
            # Anthropic streams JSON string fragments; announce once whole.
            call.args_buffer += args_delta
            try:
                args = json.loads(call.args_buffer)
            except ValueError:
                return
            if isinstance(args, dict):
                self._announce_native_call(call, args)

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
            "web_fetch",
            "code_execution",
            "url_context",
            "image_generation",
            "memory",
            "mcp_server",
        }

    def _extract_text(self, parts: Sequence[ModelResponsePart]) -> str:
        """Fallback text extraction when no stream deltas were emitted."""
        from pydantic_ai.messages import TextPart

        return join_text_parts(
            part.content for part in parts if isinstance(part, TextPart)
        )


def coerce_tool_args(args: str | dict[str, Any] | None) -> Optional[dict[str, Any]]:
    """Normalize part args (dict, JSON string, or None) to a dict or None."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None

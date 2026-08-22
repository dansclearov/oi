"""Streaming renderer that forwards response parts to the TUI as messages."""

from typing import Callable, Optional

from textual.message import Message

from oi.llm_types import ChatOptions, ModelCapabilities
from oi.renderers import ResponseRenderer


class ResponseStarted(Message):
    """A new assistant response is about to stream.

    `label_text` overrides the default assistant marker when set (e.g. a
    future `/btw` answer); None means the app's standard marker.
    """

    def __init__(self, label_text: Optional[str]) -> None:
        super().__init__()
        self.label_text = label_text


class TextDelta(Message):
    """A fragment of assistant markdown text."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ThinkingDelta(Message):
    """A fragment of a thinking trace."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolLine(Message):
    """A one-line tool call/result description."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class NativeToolCall(Message):
    """A server-side tool call started or its args resolved.

    Posted once when the call part appears (args may be None while the
    provider is still streaming them) and again when the full args are known;
    `call_id` lets the app update the same line in place.
    """

    def __init__(self, call_id: str, tool_name: str, args: Optional[dict]) -> None:
        super().__init__()
        self.call_id = call_id
        self.tool_name = tool_name
        self.args = args


class NativeToolReturn(Message):
    """A server-side tool call completed."""

    def __init__(self, call_id: str, tool_name: str, content: object) -> None:
        super().__init__()
        self.call_id = call_id
        self.tool_name = tool_name
        self.content = content


class TuiRenderer(ResponseRenderer):
    """Forwards streamed parts to the app instead of printing.

    The renderer hooks are synchronous, but widget updates (feeding the
    Markdown stream) need to be awaited — so each hook posts a message and the
    app's async handlers do the mounting/writing. The app's message queue is
    FIFO, which preserves delta ordering.
    """

    def __init__(
        self,
        capabilities: ModelCapabilities,
        options: ChatOptions,
        post: Callable[[Message], bool],
    ) -> None:
        super().__init__(capabilities, options)
        self._post = post

    def start_response(self) -> None:
        self._post(ResponseStarted(self.options.assistant_label_text))

    def record_text(self, text: str) -> None:
        # Non-streamed fallback text must still reach the screen.
        super().record_text(text)
        if text:
            self._post(TextDelta(text))

    def _render_text(self, text: str) -> None:
        self._post(TextDelta(text))

    def _render_thinking(self, text: str) -> None:
        self._post(ThinkingDelta(text))

    def _render_tool(self, text: str) -> None:
        self._post(ToolLine(text))

    def _render_native_tool_call(
        self, call_id: str, tool_name: str, args: Optional[dict]
    ) -> None:
        self._post(NativeToolCall(call_id, tool_name, args))

    def _render_native_tool_return(
        self, call_id: str, tool_name: str, content: object
    ) -> None:
        self._post(NativeToolReturn(call_id, tool_name, content))

    def _begin_thinking_section(self) -> None:
        # Thinking renders in its own widget; no separator needed.
        pass

    def _end_thinking_section(self, *, final: bool) -> None:
        pass

    def _finish(self) -> None:
        # The turn worker finalizes the response view (stops the stream).
        pass

"""Output renderers for streaming LLM responses."""

from abc import ABC, abstractmethod

from rich.console import Console
from rich.markup import escape

from llm_cli.llm_types import ChatOptions, ModelCapabilities
from llm_cli.ui.labels import AI_LABEL, rich_label


class ResponseRenderer(ABC):
    """Abstract base class for rendering streaming LLM responses."""

    def __init__(self, capabilities: ModelCapabilities, options: ChatOptions):
        self.capabilities = capabilities
        self.options = options
        self.response_content = ""
        self.was_interrupted = False
        self.thinking_section_open = False
        self.thinking_started = False
        self.content_started = False
        self.tool_output_started = False

    @abstractmethod
    def start_response(self) -> None:
        """Initialize the response rendering."""

    def render_text(self, text: str) -> None:
        """Render assistant text."""
        if not text:
            return

        if self.thinking_section_open:
            self.close_thinking_section(final=False)

        self.response_content += text
        self.content_started = True

        if not self.options.silent:
            self._render_text(text)

    def render_thinking(self, text: str) -> None:
        """Render thinking traces when available."""
        if (
            not text
            or not self.capabilities.supports_thinking
            or not self.options.enable_thinking
            or not self.options.show_thinking
            or self.options.silent
        ):
            return

        if not self.thinking_section_open:
            self._begin_thinking_section()
            self.thinking_section_open = True

        self.thinking_started = True
        self._render_thinking(text)

    def render_tool_call(self, text: str) -> None:
        """Render tool call metadata."""
        if not text or self.options.silent:
            return
        self.tool_output_started = True
        self._render_tool(text)

    def finish_response(self) -> None:
        """Finalize the response rendering."""
        if self.thinking_section_open:
            self.close_thinking_section(final=True)

        if not self.options.silent:
            self._finish()

    def get_full_response(self) -> str:
        """Get the complete response content."""
        return self.response_content

    def mark_interrupted(self) -> None:
        """Mark the response as interrupted by user."""
        self.was_interrupted = True

    def record_text(self, text: str) -> None:
        """Store text without emitting output (used for non-streamed fallbacks)."""
        if text:
            self.response_content += text
            self.content_started = True

    def close_thinking_section(self, *, final: bool) -> None:
        if not self.thinking_section_open:
            return
        if not self.options.silent:
            self._end_thinking_section(final=final)
        self.thinking_section_open = False

    def has_visible_output(self) -> bool:
        if self.options.silent:
            return False
        return self.content_started or self.thinking_started or self.tool_output_started

    @abstractmethod
    def _render_text(self, text: str) -> None: ...

    @abstractmethod
    def _render_thinking(self, text: str) -> None: ...

    @abstractmethod
    def _render_tool(self, text: str) -> None: ...

    @abstractmethod
    def _begin_thinking_section(self) -> None: ...

    @abstractmethod
    def _end_thinking_section(self, *, final: bool) -> None: ...

    @abstractmethod
    def _finish(self) -> None: ...


class StyledRenderer(ResponseRenderer):
    """Rich console renderer with styled thinking traces."""

    def __init__(self, capabilities: ModelCapabilities, options: ChatOptions):
        super().__init__(capabilities, options)
        self.console = Console(highlight=False)

    def start_response(self) -> None:
        if not self.options.silent and self.options.show_assistant_label:
            self.console.print(rich_label(AI_LABEL), end="")

    def _render_text(self, text: str) -> None:
        self.console.print(escape(text), end="")

    def _render_thinking(self, text: str) -> None:
        self.console.print(
            f"[bright_black italic]{escape(text)}[/bright_black italic]",
            end="",
        )

    def _render_tool(self, text: str) -> None:
        self.console.print(f"\n[magenta]tool:[/magenta] {escape(text)}\n", end="")

    def _begin_thinking_section(self) -> None:
        # Rich rendering already differentiates via style.
        pass

    def _end_thinking_section(self, *, final: bool) -> None:
        self.console.print("\n", end="")

    def _finish(self) -> None:
        self.console.print()

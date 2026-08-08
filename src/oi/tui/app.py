"""Full-screen chat frontend built on Textual.

Renders assistant responses as live-streamed markdown via Textual's
`MarkdownStream`, which the scrollback renderer fundamentally can't do.
Presentation is Claude-Code-style: marker glyphs instead of role labels
(`>` user, `●` assistant, colored `●` status dots), a dim one-line header
instead of the banner, and a bordered input pinned at the bottom with a
contextual hint line beneath it.
"""

import asyncio
import re
from dataclasses import replace
from typing import Optional, Sequence, Union

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    SystemPromptPart,
    UserContent,
    UserPromptPart,
)
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Markdown, Static, TextArea
from textual.widgets._markdown import MarkdownStream
from textual.worker import Worker

# oi.app is fully imported before main() branches into the TUI, so importing
# its helpers here can't create a cycle (oi.app never imports oi.tui at module
# level).
from oi.app import (
    ChatLoopContext,
    _billing_tag,
    _maybe_generate_smart_title,
    _update_title_from_first_user_message,
)
from oi.core.message_utils import flatten_history, latest_system_prompt
from oi.core.session import Chat
from oi.llm_types import ModelCapabilities
from oi.local_commands import (
    LOCAL_COMMANDS,
    build_argument_error_message,
    build_unknown_command_message,
    get_slash_prefix,
    parse_local_command,
)
from oi.registry import ModelRegistry
from oi.tui.slash_menu import SlashMenu
from oi.tui.renderer import (
    ResponseStarted,
    TextDelta,
    ThinkingDelta,
    ToolLine,
    TuiRenderer,
)
from oi.ui.image_paste import read_clipboard_image
from oi.ui.labels import (
    AI_LABEL,
    ERROR_LABEL,
    INFO_LABEL,
    SYSTEM_LABEL,
    USER_LABEL,
    WARNING_LABEL,
    LabelStyle,
)

MAX_INPUT_HEIGHT = 8

# Marker for ephemeral `/btw` side answers: hollow = not saved to the chat.
BTW_MARKER = "○ "

UserContentInput = Union[str, Sequence[UserContent]]

LABEL_CSS = {
    USER_LABEL: "user-label",
    AI_LABEL: "ai-label",
    SYSTEM_LABEL: "system-label",
    INFO_LABEL: "info-label",
    WARNING_LABEL: "warning-label",
    ERROR_LABEL: "error-label",
}

# Claude-Code-style markers shown instead of the scrollback UI's role labels.
# Statuses have no marker: they render as bare colored text lines (CC-style).
LABEL_MARKERS = {
    USER_LABEL: "❯ ",
    AI_LABEL: "● ",
    SYSTEM_LABEL: "✱ ",
    INFO_LABEL: "",
    WARNING_LABEL: "",
    ERROR_LABEL: "",
}


class Notice(Message):
    """A status line (info/warning/error) to mount in the log."""

    def __init__(self, label: LabelStyle, text: str) -> None:
        super().__init__()
        self.label = label
        self.text = text


class TurnFinished(Message):
    """The turn worker is done streaming (successfully or not)."""

    def __init__(self, interrupted: bool) -> None:
        super().__init__()
        self.interrupted = interrupted


def _row(
    label: LabelStyle, content: Static | Vertical, label_text: str | None = None
) -> Horizontal:
    """A chat row: styled marker on the left, content filling the rest."""
    return Horizontal(
        Static(
            Text(label_text or LABEL_MARKERS[label]),
            classes=f"label {LABEL_CSS[label]}",
        ),
        content,
        classes="row",
    )


def _notice_widget(label: LabelStyle, text: str) -> Horizontal | Static:
    """A status line: a marker row when the label has one, bare colored text
    otherwise (CC-style errors/warnings/info)."""
    if LABEL_MARKERS[label]:
        return _row(label, Static(Text(text), classes="content"))
    return Static(Text(text), classes=f"notice {LABEL_CSS[label]}")


class ResponseView(Vertical):
    """The content column of one assistant response.

    Sections mount lazily in arrival order: thinking trace (plain grey
    italics, exactly like the scrollback renderer), tool lines, and the
    markdown body fed through a `MarkdownStream`.
    """

    def __init__(self) -> None:
        super().__init__(classes="content")
        self._thinking: Optional[Static] = None
        self._thinking_text = ""
        self._markdown: Optional[Markdown] = None
        self._stream: Optional[MarkdownStream] = None

    async def append_thinking(self, text: str) -> None:
        if self._thinking is None:
            self._thinking = Static(classes="thinking")
            await self.mount(self._thinking)
        self._thinking_text += text
        self._thinking.update(Text(self._thinking_text.rstrip()))

    async def write_text(self, text: str) -> None:
        if self._markdown is None:
            self._markdown = Markdown()
            await self.mount(self._markdown)
            self._stream = Markdown.get_stream(self._markdown)
        assert self._stream is not None
        await self._stream.write(text)

    async def add_tool_line(self, text: str) -> None:
        await self.mount(Static(Text(f"tool: {text}"), classes="tool"))

    async def finalize(self, *, interrupted: bool) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None
        if interrupted:
            await self.mount(Static(Text("[interrupted]"), classes="interrupted"))


class ChatInput(TextArea):
    """Multi-line input: Enter submits, Shift+Enter/Ctrl+J insert a newline.

    Menu-related keys (Tab, Ctrl+N/P, Esc) are posted as `MenuKey` messages —
    the app decides what they mean based on whether the slash menu is open.
    """

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class MenuKey(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    _MENU_KEYS = {
        "tab": "complete",
        "ctrl+n": "down",
        "ctrl+p": "up",
        "escape": "dismiss",
    }

    def __init__(self) -> None:
        super().__init__()
        self.show_line_numbers = False

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        if event.key in self._MENU_KEYS:
            event.stop()
            event.prevent_default()
            self.post_message(self.MenuKey(self._MENU_KEYS[event.key]))
            return
        await super()._on_key(event)

    @property
    def text_before_cursor(self) -> str:
        return self.get_text_range((0, 0), self.cursor_location)

    def sync_height(self) -> None:
        """Grow with the (wrapped) content up to a cap, like a chat compose box.

        Only writes the style when the height actually changes — height is a
        layout property, and writing it on every keystroke would relayout the
        whole screen.
        """
        height = min(max(self.wrapped_document.height, 1), MAX_INPUT_HEIGHT)
        current = self.styles.height
        if current is None or current.value != height:
            self.styles.height = height

    def on_resize(self, event) -> None:
        self.sync_height()


class OiApp(App):
    """oi's TUI mode: same conversation model, full-screen rendering."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: ansi_default;
        color: ansi_default;
    }
    #log {
        /* Fixed region above the pinned input: stable geometry keeps
           repaints incremental while streaming. */
        height: 1fr;
        scrollbar-size-vertical: 0;
    }
    .row {
        /* Only conversation turns are rows (statuses are bare Statics), so
           this spaces user/assistant messages CC-style without re-spacing
           the header block. */
        height: auto;
        margin-bottom: 1;
    }
    .label {
        width: auto;
    }
    .content {
        width: 1fr;
        height: auto;
    }
    .header { color: ansi_bright_black; height: auto; }
    .system { color: ansi_bright_black; height: auto; }
    .notice { height: auto; }
    .user-label { color: ansi_bright_black; text-style: bold; }
    .ai-label { color: ansi_bright_white; }
    .info-label { color: ansi_bright_black; }
    .warning-label { color: ansi_yellow; }
    .error-label { color: ansi_red; }
    .thinking {
        color: ansi_bright_black;
        text-style: italic;
        height: auto;
        margin-bottom: 1;
    }
    .tool { color: ansi_magenta; height: auto; }
    .interrupted { color: ansi_bright_black; height: auto; }
    Markdown {
        padding: 0;
        margin: 0;
        background: transparent;
        height: auto;
    }
    /* No trailing blank line after a response (CLI parity: the next prompt
       follows the response directly). */
    Markdown > MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    /* Terminal-native headings: left-aligned, no banner margins. */
    MarkdownHeader {
        margin: 0 0 1 0;
    }
    MarkdownH1 {
        content-align: left middle;
    }
    #input-row {
        /* Top/bottom rules only: no side borders or padding, so the ❯ marker
           column-aligns with the conversation above. */
        height: auto;
        padding: 0;
        border-top: round ansi_bright_black;
        border-bottom: round ansi_bright_black;
    }
    #prompt-marker {
        width: auto;
        text-style: bold;
    }
    ChatInput {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }
    ChatInput:focus {
        border: none;
        background: transparent;
    }
    #hint {
        height: 1;
        padding: 0 2;
        color: ansi_bright_black;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", "Interrupt/quit", priority=True),
        Binding("escape", "interrupt", show=False),
        Binding("pageup", "scroll_log_up", show=False),
        Binding("pagedown", "scroll_log_down", show=False),
        Binding("alt+v", "paste_image", show=False),
    ]

    _IMAGE_MARKER_RE = re.compile(r"\[Image #(\d+)\]")

    def __init__(
        self,
        current_chat: Chat,
        ctx: ChatLoopContext,
        registry: ModelRegistry,
        is_new_chat: bool,
    ) -> None:
        super().__init__()
        self.theme = "ansi-dark"
        self._chat = current_chat
        self._ctx = ctx
        self._model_registry = registry
        self._is_new_chat = is_new_chat
        self._active_view: Optional[ResponseView] = None
        self._turn_worker: Optional[Worker] = None
        self._capabilities: Optional[ModelCapabilities] = None
        self._pending_images: dict[int, BinaryContent] = {}
        self._image_counter = 0

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="log")
        yield SlashMenu()
        with Horizontal(id="input-row"):
            yield Static(Text("❯ "), id="prompt-marker")
            yield ChatInput()
        yield Static(id="hint")

    async def on_mount(self) -> None:
        self._capabilities = self._ctx.llm_client.resolve_capabilities(
            self._ctx.active_model,
            self._chat.metadata.get_model_capabilities_snapshot(),
        )
        await self._replay_session_context()
        self._chat_log.anchor()
        input_widget = self.query_one(ChatInput)
        input_widget.sync_height()
        input_widget.focus()

    def _set_hint(self, text: str) -> None:
        self.query_one("#hint", Static).update(Text(text))

    @property
    def _chat_log(self) -> VerticalScroll:
        return self.query_one("#log", VerticalScroll)

    # --- startup replay -------------------------------------------------

    async def _replay_session_context(self) -> None:
        """One dim header line, the system prompt only when non-empty, then
        the conversation."""
        chat = self._chat
        tag = _billing_tag(self._model_registry, self._ctx.active_model)
        history = flatten_history(chat.messages)
        has_user_messages = any(role == "user" for role, _ in history)

        if self._is_new_chat:
            header = f"oi · {chat.metadata.model}{tag} · new chat"
        else:
            header = (
                f"oi · {chat.metadata.title} · {chat.metadata.model}{tag} "
                f"· {chat.metadata.message_count} messages"
            )
        await self._chat_log.mount(Static(Text(header), classes="header"))

        if not has_user_messages:
            system_message, from_chat = self._ctx.prompt_str, False
        else:
            system_message = latest_system_prompt(chat.messages) or ""
            from_chat = system_message != self._ctx.prompt_str
        if system_message:
            marker = "✱ (from chat) " if from_chat else "✱ "
            await self._chat_log.mount(
                Static(Text(marker + system_message), classes="system")
            )

        for role, content in history:
            if role == "user":
                await self._chat_log.mount(
                    _row(USER_LABEL, Static(Text(content), classes="content"))
                )
            else:
                view = ResponseView()
                row = _row(AI_LABEL, view)
                await self._chat_log.mount(row)
                await view.mount(Markdown(content))

    async def _mount_notice(self, label: LabelStyle, text: str) -> None:
        await self._chat_log.mount(_notice_widget(label, text))

    def _mount_notice_nowait(self, label: LabelStyle, text: str) -> None:
        """Schedule a notice row without awaiting the mount (same-frame path)."""
        self._chat_log.mount(_notice_widget(label, text))

    # --- input ----------------------------------------------------------

    @on(ChatInput.Submitted)
    async def _on_submitted(self, message: ChatInput.Submitted) -> None:
        text = message.text.strip()
        input_widget = self.query_one(ChatInput)
        if not text:
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            return

        # Clear the input and queue the echoed row without awaiting in
        # between, so both land in the same frame (no vanish-then-reappear).
        input_widget.clear()
        input_widget.sync_height()
        self._mount_notice_nowait(USER_LABEL, text)

        parsed_command = parse_local_command(text)
        if parsed_command is not None:
            command_name, command_args = parsed_command
            await self._handle_local_command(command_name, command_args)
            return

        content = self._build_user_content(text)
        self._turn_worker = self.run_worker(self._run_turn(content), exclusive=True)
        self._set_hint("esc to interrupt")

    async def _handle_local_command(self, command_name: str, command_args: str) -> None:
        if command_name not in LOCAL_COMMANDS:
            await self._mount_notice(
                WARNING_LABEL, build_unknown_command_message(command_name)
            )
            return

        if command_name == "/btw":
            question = command_args.strip()
            if not question:
                await self._mount_notice(
                    INFO_LABEL,
                    "Usage: /btw <question> — ask a one-off question with the full "
                    "conversation as context. Nothing is saved to the chat.",
                )
                return
            self._turn_worker = self.run_worker(
                self._run_btw_turn(question), exclusive=True
            )
            self._set_hint("esc to interrupt")
            return

        if command_args:
            await self._mount_notice(
                WARNING_LABEL, build_argument_error_message(command_name)
            )
            return

        if command_name == "/vim":
            await self._mount_notice(
                INFO_LABEL,
                "Vim mode isn't supported in TUI mode (it still works in the "
                "classic UI).",
            )
            return

        if command_name == "/bookmark":
            if not self._chat.should_be_saved():
                await self._mount_notice(
                    WARNING_LABEL,
                    "Bookmarking is available after the first saved exchange.",
                )
                return
            bookmarked = self._ctx.chat_manager.toggle_bookmark(self._chat)
            if bookmarked is not None:
                action = "Bookmarked" if bookmarked else "Removed bookmark from"
                await self._mount_notice(
                    INFO_LABEL, f"{action} chat: {self._chat.metadata.title}"
                )

    def _build_user_content(self, text: str) -> UserContentInput:
        """Splice pending pasted images in at their `[Image #N]` markers."""
        if not self._pending_images:
            return text

        parts: list[UserContent] = []
        pos = 0
        for match in self._IMAGE_MARKER_RE.finditer(text):
            image = self._pending_images.get(int(match.group(1)))
            if image is None:
                continue
            if match.start() > pos:
                parts.append(text[pos : match.start()])
            parts.append(image)
            pos = match.end()
        if pos < len(text):
            parts.append(text[pos:])
        self._pending_images.clear()

        if not any(isinstance(part, BinaryContent) for part in parts):
            return text
        return parts

    @on(TextArea.Changed)
    async def _on_input_changed(self, event: TextArea.Changed) -> None:
        if isinstance(event.text_area, ChatInput):
            event.text_area.sync_height()
            prefix = get_slash_prefix(
                event.text_area.text, event.text_area.text_before_cursor
            )
            await self.query_one(SlashMenu).update_filter(prefix)

    @on(ChatInput.MenuKey)
    async def _on_menu_key(self, message: ChatInput.MenuKey) -> None:
        menu = self.query_one(SlashMenu)
        if message.action == "dismiss":
            if menu.is_open:
                await menu.update_filter(None)
            else:
                self.action_interrupt()
            return
        if message.action in ("up", "down"):
            menu.move(-1 if message.action == "up" else 1)
            return
        if message.action == "complete":
            selected = menu.selected_name
            if selected is not None:
                input_widget = self.query_one(ChatInput)
                input_widget.text = selected + " "
                input_widget.move_cursor(input_widget.document.end)

    # --- streaming turn -------------------------------------------------

    async def _run_turn(self, content: UserContentInput) -> None:
        chat = self._chat
        ctx = self._ctx
        chat.append_user_message(content)
        options = replace(
            ctx.chat_options,
            notify=lambda msg: self.post_message(Notice(INFO_LABEL, msg)),
        )

        try:
            response = await ctx.llm_client.chat_async(
                chat.messages,
                ctx.active_model,
                options,
                capabilities_override=chat.metadata.get_model_capabilities_snapshot(),
                renderer_factory=lambda caps, opts: TuiRenderer(
                    caps, opts, self.post_message
                ),
            )
        except asyncio.CancelledError:
            chat.discard_pending_user_message()
            self.post_message(TurnFinished(interrupted=True))
            raise
        except Exception as exc:
            chat.discard_pending_user_message()
            self.post_message(TurnFinished(interrupted=False))
            self.post_message(
                Notice(ERROR_LABEL, f"Request failed: {type(exc).__name__}: {exc}")
            )
            return

        chat.append_assistant_response(response)
        self.post_message(TurnFinished(interrupted=False))
        if response.finish_reason == "length":
            self.post_message(
                Notice(
                    WARNING_LABEL,
                    "Response hit the model output limit. Set `max_tokens` for "
                    "this model in models.yaml if you want longer replies.",
                )
            )
        if not ctx.ephemeral:
            await asyncio.to_thread(self._persist_turn)

    async def _run_btw_turn(self, question: str) -> None:
        """Stream a `/btw` side answer against a copy of the history.

        Nothing is appended or saved; the answer renders under the hollow
        `○` marker.
        """
        chat = self._chat
        ctx = self._ctx
        side_messages = list(chat.messages)
        parts: list = []
        # On a brand-new chat the system prompt is still pending (not yet in
        # history); include it transiently without consuming it.
        if chat.pending_system_prompt:
            parts.append(SystemPromptPart(chat.pending_system_prompt))
        parts.append(UserPromptPart(question))
        side_messages.append(ModelRequest(parts=parts))

        options = replace(
            ctx.chat_options,
            assistant_label_text=BTW_MARKER,
            notify=lambda msg: self.post_message(Notice(INFO_LABEL, msg)),
        )
        try:
            await ctx.llm_client.chat_async(
                side_messages,
                ctx.active_model,
                options,
                capabilities_override=chat.metadata.get_model_capabilities_snapshot(),
                renderer_factory=lambda caps, opts: TuiRenderer(
                    caps, opts, self.post_message
                ),
            )
        except asyncio.CancelledError:
            self.post_message(TurnFinished(interrupted=True))
            raise
        except Exception as exc:
            self.post_message(TurnFinished(interrupted=False))
            self.post_message(
                Notice(ERROR_LABEL, f"Request failed: {type(exc).__name__}: {exc}")
            )
            return
        self.post_message(TurnFinished(interrupted=False))

    async def action_paste_image(self) -> None:
        if self._capabilities is None or not self._capabilities.supports_vision:
            return
        image = await asyncio.to_thread(read_clipboard_image)
        if image is None:
            await self._mount_notice(INFO_LABEL, "No image found in the clipboard.")
            return
        data, media_type = image
        self._image_counter += 1
        self._pending_images[self._image_counter] = BinaryContent(
            data=data, media_type=media_type
        )
        input_widget = self.query_one(ChatInput)
        input_widget.insert(f"[Image #{self._image_counter}] ")
        input_widget.focus()

    def _persist_turn(self) -> None:
        """Save + titling, off the event loop (smart titles call the API)."""
        _update_title_from_first_user_message(self._chat)
        self._ctx.chat_manager.save_chat(self._chat)
        _maybe_generate_smart_title(
            self._chat, self._ctx.chat_manager, self._ctx.llm_client
        )

    # --- renderer message handlers --------------------------------------

    @on(ResponseStarted)
    async def _on_response_started(self, message: ResponseStarted) -> None:
        view = ResponseView()
        self._active_view = view
        await self._chat_log.mount(_row(AI_LABEL, view, label_text=message.label_text))

    @on(TextDelta)
    async def _on_text_delta(self, message: TextDelta) -> None:
        if self._active_view is not None:
            await self._active_view.write_text(message.text)

    @on(ThinkingDelta)
    async def _on_thinking_delta(self, message: ThinkingDelta) -> None:
        if self._active_view is not None:
            await self._active_view.append_thinking(message.text)

    @on(ToolLine)
    async def _on_tool_line(self, message: ToolLine) -> None:
        if self._active_view is not None:
            await self._active_view.add_tool_line(message.text)

    @on(Notice)
    async def _on_notice(self, message: Notice) -> None:
        await self._mount_notice(message.label, message.text)

    @on(TurnFinished)
    async def _on_turn_finished(self, message: TurnFinished) -> None:
        self._set_hint("")
        if self._active_view is not None:
            await self._active_view.finalize(interrupted=message.interrupted)
            self._active_view = None

    # --- actions ---------------------------------------------------------

    def action_interrupt(self) -> None:
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._turn_worker.cancel()

    def action_interrupt_or_quit(self) -> None:
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._turn_worker.cancel()
        else:
            self._touch_and_exit()

    async def action_quit(self) -> None:
        self._touch_and_exit()

    def _touch_and_exit(self) -> None:
        # Touch the chat on exit so `oi -c` reopens the one you just closed
        # (save_chat bumps updated_at and skips empty chats itself).
        if not self._ctx.ephemeral:
            self._ctx.chat_manager.save_chat(self._chat)
        self.exit()

    def action_scroll_log_up(self) -> None:
        self._chat_log.scroll_page_up()

    def action_scroll_log_down(self) -> None:
        self._chat_log.scroll_page_down()


def run_tui(
    current_chat: Chat, ctx, registry: ModelRegistry, is_new_chat: bool
) -> None:
    """Entry point used by `main()` when the TUI knob is on."""
    OiApp(current_chat, ctx, registry, is_new_chat).run()

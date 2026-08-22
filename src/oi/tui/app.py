"""Full-screen chat frontend built on Textual.

Renders assistant responses as live-streamed markdown via Textual's
`MarkdownStream`, which the scrollback renderer fundamentally can't do.
Presentation is Claude-Code-style: marker glyphs instead of role labels
(`>` user, `●` assistant, colored `●` status dots), a dim one-line header
instead of the banner, and a bordered input pinned at the bottom with a
contextual hint line beneath it.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, Optional, Sequence, Union, cast

# pydantic_ai imports are function-local: its package __init__ costs ~600ms,
# which would otherwise delay the first paint. `main()` pre-imports it on a
# background thread while the app mounts.
if TYPE_CHECKING:
    from textual.document._document import Document

    from pydantic_ai.messages import BinaryContent, UserContent

    UserContentInput = Union[str, Sequence[UserContent]]

from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Offset, Size
from textual.message import Message
from textual.widgets import Markdown, Static, TextArea
from textual.widgets.text_area import Edit, EditResult, Selection, TextAreaTheme
from textual.timer import Timer
from textual.widgets._markdown import MarkdownStream
from textual.worker import Worker

# oi.app is fully imported before main() branches into the TUI, so importing
# its helpers here can't create a cycle (oi.app never imports oi.tui at module
# level).
from oi.app import (
    TOGGLE_SETTINGS,
    ChatLoopContext,
    _billing_tag,
    _maybe_generate_smart_title,
    _update_title_from_first_user_message,
    enable_search,
    toggle_setting,
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
from oi.tui.vim import Mode as VimMode
from oi.tui.vim import VimHandler
from oi.tui.renderer import (
    ResponseStarted,
    TextDelta,
    ThinkingDelta,
    ToolLine,
    TuiRenderer,
)
from oi import warmup
from oi.ui.image_paste import read_clipboard_image
from oi.ui.labels import (
    AI_LABEL,
    ERROR_LABEL,
    INFO_LABEL,
    PILL_RICH_STYLE,
    SYSTEM_LABEL,
    USER_LABEL,
    WARNING_LABEL,
    LabelStyle,
)

MAX_INPUT_HEIGHT = 8

# How long the "press ctrl+c again to exit" arming lasts. Long enough to read
# the hint and react, short enough that a stray later press is just a no-op.
CTRL_C_EXIT_WINDOW = 1.0
HINT_FLASH_SECONDS = 1.5

# Vim mode indicators, following vim itself: normal mode shows nothing.
VIM_MODE_HINTS = {
    VimMode.INSERT: "-- INSERT --",
    VimMode.VISUAL: "-- VISUAL --",
    VimMode.VISUAL_LINE: "-- VISUAL LINE --",
}

# Marker for ephemeral `/btw` side answers: hollow = not saved to the chat.
BTW_MARKER = "○ "

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


_IMAGE_MARKER_RE = re.compile(r"\[Image #(\d+)\]")

# An empty cursor style makes TextArea skip painting a cursor cell entirely —
# the visible cursor is the terminal's own (shaped via DECSCUSR). Styling it
# neutrally instead, which is all CSS can do, would flatten the color of
# whatever cell it lands on (e.g. the `[` of an image pill).
_NO_CURSOR_THEME = TextAreaTheme(name="oi", cursor_style=Style())


def _pill_text(text: str) -> Text:
    """Rich text with `[Image #N]` markers styled like the scrollback pills."""
    result = Text(text)
    for match in _IMAGE_MARKER_RE.finditer(text):
        result.stylize(PILL_RICH_STYLE, match.start(), match.end())
    return result


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


class ChatLog(VerticalScroll):
    """The conversation pane: bottom-anchored, told about resizes up front."""

    def preempt_resize(self, delta: int) -> None:
        """Record the height this pane is about to have, `delta` rows shorter.

        Anchored content is re-scrolled to the bottom during the layout pass,
        from the container height recorded on the *previous* pass — so a
        growing input anchors one row short and only settles on the follow-up
        pass, jumping the whole conversation a frame after every newline.
        Reporting the new height first anchors it right on the first pass; the
        follow-up pass still corrects it if this guess is off.
        """
        width, height = self._container_size
        self._container_size = Size(width, max(height - delta, 0))


class ChatInput(TextArea):
    """Multi-line input: Enter submits, Shift+Enter/Ctrl+J insert a newline.

    Menu-related keys (Tab, Ctrl+N/P, Esc) are posted as `MenuKey` messages —
    the app decides what they mean based on whether the slash menu is open.

    Pasted images live here as literal `[Image #N]` markers plus a pending
    map. On every change the markers are reconciled — images whose marker was
    mangled are dropped and survivors renumbered from 1 — so numbering is
    always contiguous within the turn. Backspace/Delete treat a marker as one
    unit (pill-like); `consume_content()` splices images in at submit and
    resets for the next turn.
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
    }

    class VimModeChanged(Message):
        """Vim mode changed; `mode` is None when vim mode is off."""

        def __init__(self, mode: Optional[VimMode]) -> None:
            super().__init__()
            self.mode = mode

    def __init__(self) -> None:
        super().__init__()
        self.show_line_numbers = False
        # The hardware terminal cursor is used instead (steady, mode-shaped);
        # the painted cursor is never drawn and must not blink in tests.
        self.cursor_blink = False
        self.register_theme(_NO_CURSOR_THEME)
        self.theme = _NO_CURSOR_THEME.name
        # Both would stylize whole cells last, flattening the image pills'
        # color, and neither earns its keep in a prose input.
        self.highlight_cursor_line = False
        self.match_cursor_bracket = False
        self.vim: Optional[VimHandler] = None
        self._images: dict[int, BinaryContent] = {}

    def set_vim_enabled(self, enabled: bool) -> None:
        if enabled and self.vim is None:
            self.vim = VimHandler(
                self,
                on_mode_change=lambda mode: self.post_message(
                    self.VimModeChanged(mode)
                ),
                # Image markers behave as single characters under vim motions.
                atom_spans=lambda: [
                    (start, end) for start, end, _ in self._intact_markers()
                ],
            )
            self.post_message(self.VimModeChanged(self.vim.mode))
        elif not enabled and self.vim is not None:
            self.vim = None
            self.post_message(self.VimModeChanged(None))

    def vim_reset(self) -> None:
        """Back to insert mode for the next message (like a fresh prompt)."""
        if self.vim is not None:
            self.vim.enter_insert()

    def vim_escape(self) -> bool:
        """Esc with no menu open. True when the vim layer consumed it."""
        if self.vim is None:
            return False
        return self.vim.handle_escape()

    @property
    def _menu_is_open(self) -> bool:
        return self.screen.query_one(SlashMenu).is_open

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
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            # Decided here rather than in the app: a mode change routed through
            # the app's message queue lands *after* the keys typed right behind
            # Esc, so those keys would run as insert-mode text.
            if self._menu_is_open:
                self.post_message(self.MenuKey("dismiss"))
            elif not self.vim_escape():
                self.post_message(self.MenuKey("interrupt"))
            return
        if event.key in self._MENU_KEYS:
            event.stop()
            event.prevent_default()
            self.post_message(self.MenuKey(self._MENU_KEYS[event.key]))
            return
        if (
            self.vim is not None
            and self.vim.mode is not VimMode.INSERT
            and (event.is_printable or event.key == "ctrl+r")
        ):
            event.stop()
            event.prevent_default()
            self.vim.handle_key(event)
            return
        if event.key in ("backspace", "delete") and self._delete_marker_atomically(
            before=event.key == "backspace"
        ):
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)

    @property
    def text_before_cursor(self) -> str:
        return self.get_text_range((0, 0), self.cursor_location)

    # ---------------------------------------------------------- image pills

    def attach_image(self, content: BinaryContent) -> None:
        """Register a pasted image and insert its marker at the cursor."""
        self._reconcile_image_markers()
        number = len(self._images) + 1
        self._images[number] = content
        self.insert(f"[Image #{number}] ")

    def consume_content(self, text: str) -> UserContentInput:
        """Splice pending images in at their markers; reset for the next turn."""
        images = self._images
        self._images = {}
        if not images:
            return text
        # Reached only after a paste, which already forced the pydantic_ai
        # import — safe on the main thread.
        from pydantic_ai.messages import BinaryContent

        parts: list[UserContent] = []
        pos = 0
        for match in _IMAGE_MARKER_RE.finditer(text):
            image = images.get(int(match.group(1)))
            if image is None:
                continue
            if match.start() > pos:
                parts.append(text[pos : match.start()])
            parts.append(image)
            pos = match.end()
        if pos < len(text):
            parts.append(text[pos:])

        if not any(isinstance(part, BinaryContent) for part in parts):
            return text
        return parts

    @on(TextArea.Changed)
    def _on_self_changed(self) -> None:
        self._reconcile_image_markers()

    def _watch_selection(
        self, previous_selection: Selection, selection: Selection
    ) -> None:
        super()._watch_selection(previous_selection, selection)
        # Keep the cursor out of the middle of a marker in every mode (arrows,
        # Home/End, mouse) — the scrollback UI gets this for free from its
        # one-character sentinel pills. Re-entering the watcher is safe: the
        # snapped position is outside every marker, so it settles at once.
        # (getattr: the reactive fires during TextArea.__init__, before ours.)
        # TextArea points the terminal cursor at the row it measures now;
        # correct it while a resize is still pending (vim moves the cursor
        # after the edit that changed the height, so this is that path).
        self._sync_terminal_cursor()
        if not getattr(self, "_images", None):
            return
        snapped = self._snap_selection(previous_selection, selection)
        if snapped != selection:
            self.selection = snapped

    def _snap_selection(
        self, previous_selection: Selection, selection: Selection
    ) -> Selection:
        document = cast("Document", self.document)
        spans = [(start, end) for start, end, _ in self._intact_markers()]
        if not spans:
            return selection

        start_idx = document.get_index_from_location(selection.start)
        end_idx = document.get_index_from_location(selection.end)
        previous_idx = document.get_index_from_location(previous_selection.end)

        new_end = self._snap_index(end_idx, previous_idx, spans)
        if selection.is_empty:
            new_start = new_end
        else:
            # Anchor snaps outward so a drag covers whole markers.
            new_start = self._snap_index(
                start_idx, end_idx if start_idx < end_idx else -1, spans
            )
        if (new_start, new_end) == (start_idx, end_idx):
            return selection
        return Selection(
            document.get_location_from_index(new_start),
            document.get_location_from_index(new_end),
        )

    @staticmethod
    def _snap_index(idx: int, coming_from: int, spans: list[tuple[int, int]]) -> int:
        """Move idx to the nearer edge of any marker it landed inside."""
        for start, end in spans:
            if start < idx < end:
                if coming_from <= start:
                    return end
                if coming_from >= end:
                    return start
                return start if idx - start < end - idx else end
        return idx

    def get_line(self, line_index: int) -> Text:
        """The line as rendered, with intact image markers styled as pills.

        The scrollback UI gets this from `PillProcessor`; here it rides on
        TextArea's documented per-line styling hook. Every edit rebuilds the
        highlight map, which clears the rendered-line cache, so styling can't
        go stale against a renumbered marker.
        """
        line = super().get_line(line_index)
        if not self._images:
            return line
        document = cast("Document", self.document)
        for start, end, _ in self._intact_markers():
            start_row, start_column = document.get_location_from_index(start)
            if start_row != line_index:
                continue
            # A marker never spans lines, so the end is on this row too.
            _, end_column = document.get_location_from_index(end)
            line.stylize(PILL_RICH_STYLE, start_column, end_column)
        return line

    def _intact_markers(self) -> list[tuple[int, int, int]]:
        """(start, end, number) spans of markers backed by a pending image."""
        seen: set[int] = set()
        spans = []
        for match in _IMAGE_MARKER_RE.finditer(self.text):
            number = int(match.group(1))
            if number in self._images and number not in seen:
                seen.add(number)
                spans.append((match.start(), match.end(), number))
        return spans

    def _reconcile_image_markers(self) -> None:
        """Drop images whose marker was edited away; renumber the rest from 1.

        Renumbering keeps `len(images) + 1` correct as the next paste number
        and matches the scrollback UI, which renumbers pills on deletion.
        """
        if not self._images:
            return
        spans = self._intact_markers()
        renames = [
            (start, end, old, new)
            for new, (start, end, old) in enumerate(spans, start=1)
            if old != new
        ]
        self._images = {
            new: self._images[old] for new, (_, _, old) in enumerate(spans, start=1)
        }
        for start, end, _, new in reversed(renames):
            self.replace(
                f"[Image #{new}]", self._marker_loc(start), self._marker_loc(end)
            )

    def _marker_loc(self, index: int):
        return cast("Document", self.document).get_location_from_index(index)

    def _delete_marker_atomically(self, *, before: bool) -> bool:
        """Delete the whole `[Image #N]` marker at the cursor edge, if any."""
        if not self._images or not self.selection.is_empty:
            return False
        document = cast("Document", self.document)
        idx = document.get_index_from_location(self.cursor_location)
        target = idx - 1 if before else idx
        for start, end, _ in self._intact_markers():
            if start <= target < end:
                self.delete(self._marker_loc(start), self._marker_loc(end))
                return True
        return False

    def edit(self, edit: Edit) -> EditResult:
        """Apply an edit, then re-measure everything it changed.

        Both steps have to happen here rather than off the `Changed` message
        the edit posts, because a frame can be painted before that message is
        handled — the input was seen keeping its old height for a frame after
        a vim `dd`. `scroll_cursor_visible` runs again because TextArea
        scrolls to the cursor mid-edit, before `_refresh_size` updates the
        virtual size, so past the height cap the caret trails the line being
        typed by a frame.
        """
        result = super().edit(edit)
        self.scroll_cursor_visible()
        self.sync_height()
        return result

    def undo(self) -> None:
        super().undo()
        self.sync_height()

    def redo(self) -> None:
        super().redo()
        self.sync_height()

    def sync_height(self) -> None:
        """Grow with the (wrapped) content up to a cap, like a chat compose box.

        Only writes the style when the height actually changes — height is a
        layout property, and writing it on every keystroke would relayout the
        whole screen.
        """
        height = min(max(self.wrapped_document.height, 1), MAX_INPUT_HEIGHT)
        current = self.styles.height
        if current is None or current.value != height:
            delta = height - int(current.value) if current is not None else 0
            self.styles.height = height
            if delta:
                # The pane above loses exactly the rows the input gained.
                self.screen.query_one(ChatLog).preempt_resize(delta)
            self._sync_terminal_cursor()
            self.call_after_refresh(self._sync_terminal_cursor)

    def _pending_resize_delta(self) -> int:
        """Rows the input is about to gain once the layout catches up."""
        height = self.styles.height
        if height is None:
            return 0
        return int(height.value) - self.size.height

    def _terminal_cursor_offset(self) -> Offset:
        """Where the caret belongs, accounting for a resize still in flight.

        `cursor_screen_offset` measures against the geometry the input has
        right now, so between a height change and the layout pass it names the
        row the caret is leaving — deleting a newline would paint it a row
        high and drop it back a frame later. The input is pinned to the
        bottom, so its top moves by the pending delta, and its own scroll is
        about to be zero: the height only changes while the wrapped text fits
        (past the cap it stays pinned at the cap, scrolling instead).
        """
        offset = self.cursor_screen_offset
        delta = self._pending_resize_delta()
        if not delta:
            return offset
        return Offset(offset.x, offset.y + self.scroll_offset.y - delta)

    def _sync_terminal_cursor(self) -> None:
        """Re-point the terminal cursor after the widget moves.

        The input is pinned to the bottom, so resizing it shifts its whole
        region; Textual only refreshes `app.cursor_position` when the
        selection, scroll, or focus changes, which would leave the hardware
        cursor on the row the input used to occupy (its border).

        `cursor_position` is a plain attribute that is only written to the
        terminal while rendering a compositor update, so the repaint is what
        actually moves the visible cursor — without it the value is right but
        the cursor doesn't budge until the next keystroke repaints.
        """
        if not self.has_focus:
            return
        offset = self._terminal_cursor_offset()
        if self.app.cursor_position == offset:
            return
        self.app.cursor_position = offset
        self.refresh()

    def on_resize(self, event) -> None:
        self.sync_height()
        self.call_after_refresh(self._sync_terminal_cursor)


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
    /* Size tables to their content instead of the full pane width. Textual's
       default is `width: 1fr`, which also flips MarkdownTableContent's grid to
       expand=True; `auto` sizes the columns to the cells and keeps the shrink
       path for tables wider than the pane. */
    MarkdownTable {
        width: auto;
        max-width: 100%;
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
        /* Past the height cap the input scrolls, but a visible scrollbar
           would narrow the wrap width and re-wrap everything already typed. */
        scrollbar-size-vertical: 0;
    }
    ChatInput:focus {
        border: none;
        background: transparent;
    }
    /* nvim-style visual selection: paint a background and leave the text's
       own color alone, so it reads as a layer over the text. Textual's ansi
       theme would otherwise use `text-style: reverse`, which swaps fg/bg and
       makes dim text (image markers) look like it vanished. */
    ChatInput .text-area--selection {
        background: ansi_bright_black !important;
        text-style: none !important;
    }
    #hint {
        height: 1;
        padding: 0 2;
        color: ansi_bright_black;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", "Interrupt/quit", priority=True),
        # Cmd+C, in terminals that forward it to the app when they have no
        # selection of their own (Ghostty's performable keybinds, kitty).
        # Priority: ChatInput's TextArea binds super+c too and would consume
        # it before a screen selection could be copied.
        Binding("super+c", "copy_selection", show=False, priority=True),
        Binding("escape", "interrupt", show=False),
        Binding("pageup", "scroll_log_up", show=False),
        Binding("pagedown", "scroll_log_down", show=False),
        # Ctrl+V is the chord Mac terminals leave free (their paste is Cmd+V);
        # Linux terminals that bind it to paste never deliver it, so it's
        # inert there and Alt+V stays the Linux chord. Priority: TextArea
        # binds ctrl+v to its internal-clipboard paste.
        Binding("alt+v", "paste_image", show=False),
        Binding("ctrl+v", "paste_image", show=False, priority=True),
    ]

    def __init__(
        self,
        current_chat: Chat,
        ctx: ChatLoopContext,
        registry: ModelRegistry,
        is_new_chat: bool,
    ) -> None:
        super().__init__()
        self.theme = "ansi-dark"
        # Textual tags every markdown table cell with a tooltip repeating the
        # cell's own text; nothing in oi sets a tooltip worth showing, so the
        # Tooltip widget is kept off the screen entirely.
        self._disable_tooltips = True
        # Per wheel *event*, and terminals send three per notch — one line each
        # keeps a notch at the three lines a terminal scrolls by convention.
        self.scroll_sensitivity_y = 1.0
        self._chat = current_chat
        self._ctx = ctx
        self._model_registry = registry
        self._is_new_chat = is_new_chat
        self._active_view: Optional[ResponseView] = None
        self._response_active = False
        self._response_label: Optional[str] = None
        self._hint_timer: Optional[Timer] = None
        self._exit_armed_at: Optional[float] = None
        self._vim_mode: Optional[VimMode] = None
        self._turn_active = False
        self._turn_worker: Optional[Worker] = None
        self._capabilities: Optional[ModelCapabilities] = None
        self._header_message_count = current_chat.metadata.message_count

    def compose(self) -> ComposeResult:
        # can_focus=False: clicking the log to start a mouse selection must not
        # steal focus from the input, or typing afterwards goes nowhere.
        # Keyboard scrolling stays available through the app-level bindings.
        yield ChatLog(id="log", can_focus=False)
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
        input_widget.set_vim_enabled(self._ctx.config.vim_mode)
        input_widget.sync_height()
        input_widget.focus()
        # Show the terminal's own cursor (Textual keeps it positioned at the
        # TextArea cursor for IME); steady bar to start — insert mode.
        self._write_terminal("\x1b[?25h\x1b[6 q")
        # After the first paint: pre-import pydantic_ai so the first turn
        # doesn't stall on it. The turn and paste paths gate on ensure().
        self.call_after_refresh(warmup.warm)

    def _write_terminal(self, sequence: str) -> None:
        if self._driver is not None and not self.is_headless:
            self._driver.write(sequence)

    @on(ChatInput.VimModeChanged)
    def _on_vim_mode_changed(self, message: ChatInput.VimModeChanged) -> None:
        self._vim_mode = message.mode
        # Steady bar in insert (and with vim off), steady block otherwise.
        bar = message.mode in (None, VimMode.INSERT)
        self._write_terminal("\x1b[6 q" if bar else "\x1b[2 q")
        self._refresh_hint()

    def _set_hint(self, text: str) -> None:
        if self._hint_timer is not None:
            self._hint_timer.stop()
            self._hint_timer = None
        # layout=False: the hint is a fixed-height row, and Textual's layout
        # pass walks every widget in the log — so the default would relayout
        # the whole conversation to rewrite one line.
        self.query_one("#hint", Static).update(Text(text), layout=False)

    def _refresh_hint(self) -> None:
        """Show the hint the current state calls for.

        Segments, vim-style: the mode indicator first (nothing in normal mode,
        nothing when vim is off), then any contextual hint. Flashes override
        the whole line until they lapse.
        """
        segments = [VIM_MODE_HINTS.get(self._vim_mode), self._turn_hint()]
        self._set_hint("  ".join(part for part in segments if part))

    def _turn_hint(self) -> str:
        return "esc to interrupt" if self._turn_active else ""

    def _flash_hint(self, text: str, seconds: float) -> None:
        """Show a hint that reverts to the state's own hint on its own."""
        self._set_hint(text)
        self._hint_timer = self.set_timer(seconds, self._restore_hint)

    def _restore_hint(self) -> None:
        self._exit_armed_at = None
        self._refresh_hint()

    @property
    def _chat_log(self) -> ChatLog:
        return self.query_one("#log", ChatLog)

    def _search_active(self) -> bool:
        """Whether this session's turns actually get a web search tool.

        `--search` is dropped by the client on models without the capability,
        so the header must agree with that.
        """
        return bool(
            self._ctx.chat_options.enable_search
            and self._capabilities is not None
            and self._capabilities.supports_search
        )

    def _header_text(self) -> str:
        chat = self._chat
        tag = _billing_tag(self._model_registry, self._ctx.active_model)
        # Only shown when search is actually in play — its absence means off.
        search = " · search" if self._search_active() else ""
        if self._is_new_chat:
            return f"oi · {chat.metadata.model}{tag}{search} · new chat"
        return (
            f"oi · {chat.metadata.title} · {chat.metadata.model}{tag}{search} "
            f"· {self._header_message_count} messages"
        )

    def _refresh_header(self) -> None:
        self.query_one("#header", Static).update(Text(self._header_text()))

    # --- startup replay -------------------------------------------------

    async def _replay_session_context(self) -> None:
        """One dim header line, the system prompt only when non-empty, then
        the conversation."""
        chat = self._chat
        history = flatten_history(chat.messages)
        has_user_messages = any(role == "user" for role, _ in history)

        # The counts describe the session as opened; only the search segment
        # moves after this (via `/search`), so the rest is frozen here.
        self._header_message_count = chat.metadata.message_count
        await self._chat_log.mount(
            Static(Text(self._header_text()), classes="header", id="header")
        )

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
                await self._mount_user_row(content)
            else:
                view = ResponseView()
                row = _row(AI_LABEL, view)
                await self._chat_log.mount(row)
                await view.mount(Markdown(content))

    async def _mount_notice(self, label: LabelStyle, text: str) -> None:
        await self._chat_log.mount(_notice_widget(label, text))

    async def _mount_user_row(self, text: str) -> None:
        """Echo a user message, keeping image markers styled as pills."""
        await self._chat_log.mount(
            _row(USER_LABEL, Static(_pill_text(text), classes="content"))
        )

    # --- input ----------------------------------------------------------

    @on(ChatInput.Submitted)
    async def _on_submitted(self, message: ChatInput.Submitted) -> None:
        text = message.text.strip()
        input_widget = self.query_one(ChatInput)
        if not text:
            return
        # `_turn_active` rather than the worker: it is set synchronously here,
        # and the worker only starts a frame later.
        if self._turn_active:
            return

        parsed_command = parse_local_command(text)
        # Take the images before clearing the input (clearing reconciles the
        # marker registry down to nothing).
        content = None if parsed_command else input_widget.consume_content(text)

        # Hold the paint until the echoed row is actually mounted, so the
        # input clearing and the row appearing are one frame. Mounting takes a
        # refresh cycle of its own, so without the batch the input empties a
        # frame (plus a full relayout) before the message shows up.
        with self.batch_update():
            input_widget.clear()
            input_widget.vim_reset()
            input_widget.sync_height()
            await self._mount_user_row(text)

        if parsed_command is not None:
            command_name, command_args = parsed_command
            await self._handle_local_command(command_name, command_args)
            return

        assert content is not None
        self._exit_armed_at = None
        self._turn_active = True
        self._refresh_hint()
        # Starting the turn blocks the event loop for as long as pydantic-ai
        # takes to build the model — hundreds of ms on the first turn of a run,
        # when it also imports the provider SDK. Wait for the echoed row to be
        # on screen before paying that, or the message looks stuck in the input.
        self.call_after_refresh(self._start_turn, content)

    def _start_turn(self, content: UserContentInput) -> None:
        self._turn_worker = self.run_worker(self._run_turn(content), exclusive=True)

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
            self._turn_active = True
            self._refresh_hint()
            return

        if command_args:
            await self._mount_notice(
                WARNING_LABEL, build_argument_error_message(command_name)
            )
            return

        setting = TOGGLE_SETTINGS.get(command_name)
        if setting is not None:
            label, message = toggle_setting(setting, self._ctx.config)
            if setting.key == "vim_mode":
                self.query_one(ChatInput).set_vim_enabled(self._ctx.config.vim_mode)
            await self._mount_notice(label, message)
            return

        if command_name == "/search":
            label, message = enable_search(
                self._ctx.chat_options,
                self._capabilities or ModelCapabilities(),
                self._chat.metadata.model,
            )
            self._refresh_header()
            await self._mount_notice(label, message)
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
            await menu.update_filter(None)
            return
        if message.action == "interrupt":
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
        await asyncio.to_thread(warmup.ensure)
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
        await asyncio.to_thread(warmup.ensure)
        from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

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
        await asyncio.to_thread(warmup.ensure)
        from pydantic_ai.messages import BinaryContent

        image = await asyncio.to_thread(read_clipboard_image)
        if image is None:
            await self._mount_notice(INFO_LABEL, "No image found in the clipboard.")
            return
        data, media_type = image
        input_widget = self.query_one(ChatInput)
        input_widget.attach_image(BinaryContent(data=data, media_type=media_type))
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
    def _on_response_started(self, message: ResponseStarted) -> None:
        # Don't mount anything yet: the marker appears with the first piece of
        # output, not at submit time (the request is still in flight here).
        self._response_active = True
        self._response_label = message.label_text
        self._active_view = None

    async def _ensure_response_view(self) -> ResponseView:
        if self._active_view is None:
            view = ResponseView()
            self._active_view = view
            await self._chat_log.mount(
                _row(AI_LABEL, view, label_text=self._response_label)
            )
        return self._active_view

    @on(TextDelta)
    async def _on_text_delta(self, message: TextDelta) -> None:
        if self._response_active:
            await (await self._ensure_response_view()).write_text(message.text)

    @on(ThinkingDelta)
    async def _on_thinking_delta(self, message: ThinkingDelta) -> None:
        if self._response_active:
            await (await self._ensure_response_view()).append_thinking(message.text)

    @on(ToolLine)
    async def _on_tool_line(self, message: ToolLine) -> None:
        if self._response_active:
            await (await self._ensure_response_view()).add_tool_line(message.text)

    @on(Notice)
    async def _on_notice(self, message: Notice) -> None:
        await self._mount_notice(message.label, message.text)

    @on(TurnFinished)
    async def _on_turn_finished(self, message: TurnFinished) -> None:
        self._turn_active = False
        self._refresh_hint()
        self._response_active = False
        if message.interrupted and self._active_view is None:
            # Interrupted before any output arrived: still show it was cancelled.
            await self._ensure_response_view()
        if self._active_view is not None:
            await self._active_view.finalize(interrupted=message.interrupted)
            self._active_view = None
        self._response_label = None

    # --- actions ---------------------------------------------------------

    def action_interrupt(self) -> None:
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._turn_worker.cancel()

    def action_interrupt_or_quit(self) -> None:
        """Ctrl+C: interrupt a stream, else copy a selection, else exit.

        Each press has exactly one meaning, and only a bare press (nothing
        streaming, nothing selected) arms the exit — so copying twice in a row
        can never quit the app.
        """
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._turn_worker.cancel()
            return

        if self._copy_selection():
            return

        now = monotonic()
        if (
            self._exit_armed_at is not None
            and now - self._exit_armed_at <= CTRL_C_EXIT_WINDOW
        ):
            self._touch_and_exit()
            return
        self._exit_armed_at = now
        self._flash_hint("press ctrl+c again to exit", CTRL_C_EXIT_WINDOW)

    def _copy_selection(self) -> bool:
        """Copy the screen selection if there is one. True when copied."""
        selected = self.screen.get_selected_text()
        if not selected:
            return False
        self.copy_to_clipboard(selected)
        self.clear_selection()
        self._exit_armed_at = None
        self._flash_hint("copied to clipboard", HINT_FLASH_SECONDS)
        self.query_one(ChatInput).focus()
        return True

    def action_copy_selection(self) -> None:
        """Cmd+C: copy only — never interrupts, never arms the exit."""
        self._copy_selection()

    async def action_quit(self) -> None:
        self._touch_and_exit()

    def _touch_and_exit(self) -> None:
        # Touch the chat on exit so `oi -c` reopens the one you just closed
        # (save_chat bumps updated_at and skips empty chats itself).
        if not self._ctx.ephemeral:
            self._ctx.chat_manager.save_chat(self._chat)
        # DECSCUSR shape outlives the app; restore the terminal default.
        self._write_terminal("\x1b[0 q")
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

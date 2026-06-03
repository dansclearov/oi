"""Interactive chat selection UI.

This module provides cross-platform keyboard input handling:
- Windows: Uses msvcrt for single-key input
- Unix/Linux/macOS: Uses termios/tty for raw terminal input
- Rich console output works consistently across all platforms
"""

import os
import shlex
import subprocess
import sys
import tempfile
from typing import Callable, Optional

from rich.console import Console, Group
from rich.control import Control
from rich.live import Live
from rich.rule import Rule
from rich.segment import Segment, Segments
from rich.text import Text

from oi.constants import (
    DEFAULT_PAGE_SIZE,
    INITIAL_PAGE,
    INITIAL_SELECTED_INDEX,
    MAX_TITLE_LENGTH,
    NAVIGATION_KEYS,
    PREVIEW_MAX_HEIGHT,
    PREVIEW_MIN_HEIGHT,
    PREVIEW_SCROLL_LINES,
)
from oi.core.session import Chat, ChatMetadata
from oi.ui.labels import INFO_LABEL, rich_message
from oi.ui.transcript import plaintext_transcript, search_blob, styled_transcript

CTRL_P = "\x10"
CTRL_N = "\x0e"
ESC = "\x1b"
TAB = "\t"
BACKSPACE = ("\x7f", "\x08")


class ChatSelector:
    """Interactive chat selection with keyboard navigation."""

    def __init__(self, console: Console):
        self.console = console

    def select_chat(
        self,
        chats: list[ChatMetadata],
        *,
        load_chat: Callable[[str], Optional[Chat]],
        delete_chat: Callable[[str], None],
        toggle_bookmark: Callable[[ChatMetadata], bool | None],
    ) -> Optional[Chat]:
        """Interactive chat selection with keyboard navigation."""
        if not chats:
            self.console.print(
                rich_message(INFO_LABEL, "No existing chats found.", dim=True)
            )
            return None

        all_chats = chats
        bookmarked_only = False
        page_size = DEFAULT_PAGE_SIZE
        current_page = INITIAL_PAGE
        selected_index = INITIAL_SELECTED_INDEX

        search_mode = False
        search_query = ""
        search_index: dict[str, str] | None = None

        preview_open = False
        preview_scroll = 0
        last_preview_id: str | None = None
        # Rendered (total_lines, height) of the preview, set during render so the
        # scroll-down handler can clamp without re-measuring.
        preview_view = {"total": 0, "height": 0}

        # Cache loaded chats by id (None == unreadable) so navigation/search
        # never re-read the same transcript from disk.
        chat_cache: dict[str, Optional[Chat]] = {}

        def get_chat(chat_id: str) -> Optional[Chat]:
            if chat_id not in chat_cache:
                chat_cache[chat_id] = load_chat(chat_id)
            return chat_cache[chat_id]

        def build_search_index() -> dict[str, str]:
            index = {}
            for meta in all_chats:
                chat = get_chat(meta.id)
                index[meta.id] = (
                    search_blob(meta.title, chat)
                    if chat is not None
                    else meta.title.lower()
                )
            return index

        def visible() -> list[ChatMetadata]:
            chats_ = self._filter_chats(all_chats, bookmarked_only)
            index = search_index
            if search_query and index is not None:
                query = search_query.lower()
                chats_ = [
                    chat
                    for chat in chats_
                    if query in index.get(chat.id, chat.title.lower())
                ]
            return chats_

        def render_selection():
            visible_chats = visible()
            page_chats = self._get_page_chats(visible_chats, current_page, page_size)
            output = []

            scope = "bookmarked" if bookmarked_only else "all"
            header = f"Select a chat to continue ({scope} chats, {current_page + 1}/{total_pages}):"
            output.append(header)
            if search_mode or search_query:
                cursor = "_" if search_mode else ""
                output.append(f"[cyan]Search:[/cyan] {search_query}{cursor}")
            output.append("")

            if not visible_chats:
                if search_query:
                    output.append("[dim]No chats match the search.[/dim]")
                else:
                    output.append(
                        "[dim]No bookmarked chats. Press f to show all chats.[/dim]"
                    )
                output.append("")
                output.append(
                    self._help_line(
                        total_pages, search_mode, preview_open, bool(search_query)
                    )
                )
                return "\n".join(output)

            for i, chat in enumerate(page_chats):
                date_str = chat.updated_at.strftime("%Y-%m-%d %H:%M")
                title = (
                    chat.title[:MAX_TITLE_LENGTH] + "..."
                    if len(chat.title) > MAX_TITLE_LENGTH
                    else chat.title
                )
                bookmark_marker = "★" if chat.bookmarked else " "
                meta = f"[bright_black]({chat.model}, {chat.message_count} msgs)[/bright_black]"

                if i == selected_index and not search_mode:
                    output.append(
                        f"[bright_yellow]❯ {i + 1:2}. {bookmark_marker} [{date_str}] {title:<78} "
                        f"{meta}[/bright_yellow]"
                    )
                elif i == selected_index:
                    output.append(
                        f"[cyan]❯ {i + 1:2}. {bookmark_marker} [{date_str}] {title:<78} [/cyan]{meta}"
                    )
                else:
                    output.append(
                        f"  {i + 1:2}. {bookmark_marker} [{date_str}] {title:<78} {meta}"
                    )

            output.append("")
            output.append(
                self._help_line(
                    total_pages, search_mode, preview_open, bool(search_query)
                )
            )

            list_str = "\n".join(output)
            if not preview_open:
                return list_str

            current_meta = page_chats[selected_index] if page_chats else None
            preview = self._render_preview(
                get_chat(current_meta.id) if current_meta else None,
                list_lines=len(output),
                scroll=preview_scroll,
                view=preview_view,
            )
            return Group(Text.from_markup(list_str), preview)

        current_page, selected_index, total_pages = self._clamp_selection_state(
            visible(), page_size, current_page, selected_index
        )

        try:
            with Live(
                render_selection(), console=self.console, refresh_per_second=10
            ) as live:
                while True:
                    visible_chats = visible()
                    current_page, selected_index, total_pages = (
                        self._clamp_selection_state(
                            visible_chats, page_size, current_page, selected_index
                        )
                    )
                    page_chats = self._get_page_chats(
                        visible_chats, current_page, page_size
                    )

                    current_id = page_chats[selected_index].id if page_chats else None
                    if current_id != last_preview_id:
                        last_preview_id = current_id
                        preview_scroll = 0

                    key = self._read_key()

                    if search_mode:
                        if key == ESC:
                            # Clear the filter and leave search mode entirely.
                            search_mode = False
                            search_query = ""
                        elif key in NAVIGATION_KEYS["ENTER"]:
                            # Commit the filter: keep the matched list, hand the
                            # normal navigation keybinds back to the user.
                            search_mode = False
                        elif key in BACKSPACE:
                            search_query = search_query[:-1]
                        elif key == TAB:
                            preview_open = not preview_open
                        elif key == "\x1b[A":
                            current_page, selected_index = self._select_prev(
                                visible_chats, page_size, current_page, selected_index
                            )
                        elif key == "\x1b[B":
                            current_page, selected_index = self._select_next(
                                visible_chats,
                                page_size,
                                current_page,
                                selected_index,
                                total_pages,
                            )
                        elif preview_open and key == CTRL_P:
                            preview_scroll = max(
                                0, preview_scroll - PREVIEW_SCROLL_LINES
                            )
                        elif preview_open and key == CTRL_N:
                            preview_scroll = self._scroll_down(
                                preview_scroll, preview_view
                            )
                        elif len(key) == 1 and key.isprintable():
                            search_query += key

                        live.update(render_selection())
                        continue

                    if key == "/":
                        search_mode = True
                        if search_index is None:
                            search_index = build_search_index()

                    elif key == ESC and search_query:
                        search_query = ""

                    elif key == TAB:
                        preview_open = not preview_open

                    elif key == "e" and page_chats:
                        chat = get_chat(page_chats[selected_index].id)
                        if chat is not None:
                            self._open_in_editor(chat, live)

                    elif preview_open and key == CTRL_P:
                        preview_scroll = max(0, preview_scroll - PREVIEW_SCROLL_LINES)

                    elif preview_open and key == CTRL_N:
                        preview_scroll = self._scroll_down(preview_scroll, preview_view)

                    elif preview_open and key == "G":
                        preview_scroll = max(
                            0, preview_view["total"] - preview_view["height"]
                        )

                    elif preview_open and key == "g":
                        if self._read_key() == "g":
                            preview_scroll = 0

                    elif key in NAVIGATION_KEYS["UP"] and page_chats:
                        current_page, selected_index = self._select_prev(
                            visible_chats, page_size, current_page, selected_index
                        )

                    elif key in NAVIGATION_KEYS["DOWN"] and page_chats:
                        current_page, selected_index = self._select_next(
                            visible_chats,
                            page_size,
                            current_page,
                            selected_index,
                            total_pages,
                        )

                    elif key in NAVIGATION_KEYS["ENTER"] and page_chats:
                        selected_chat = page_chats[selected_index]
                        loaded_chat = load_chat(selected_chat.id)
                        if loaded_chat is not None:
                            return loaded_chat

                        all_chats = self._refresh_chat_list(all_chats, selected_chat.id)
                        if not all_chats:
                            return None

                    elif (
                        key in NAVIGATION_KEYS["NEXT_PAGE"]
                        and current_page < total_pages - 1
                    ):
                        current_page += 1
                        selected_index = INITIAL_SELECTED_INDEX

                    elif key in NAVIGATION_KEYS["PREV_PAGE"] and current_page > 0:
                        current_page -= 1
                        selected_index = INITIAL_SELECTED_INDEX

                    elif key == NAVIGATION_KEYS["BOOKMARK"] and page_chats:
                        toggle_bookmark(page_chats[selected_index])

                    elif key == NAVIGATION_KEYS["FILTER_BOOKMARKED"]:
                        bookmarked_only = not bookmarked_only

                    elif key == NAVIGATION_KEYS["DELETE"]:
                        second_key = self._read_key()
                        if second_key == NAVIGATION_KEYS["DELETE"] and page_chats:
                            selected_chat = page_chats[selected_index]
                            delete_chat(selected_chat.id)
                            chat_cache.pop(selected_chat.id, None)
                            if search_index is not None:
                                search_index.pop(selected_chat.id, None)
                            all_chats = self._refresh_chat_list(
                                all_chats, selected_chat.id
                            )
                            if not all_chats:
                                return None

                    elif key in NAVIGATION_KEYS["QUIT"]:
                        return None

                    live.update(render_selection())

        except KeyboardInterrupt:
            return None

    def _help_line(
        self,
        total_pages: int,
        search_mode: bool,
        preview_open: bool,
        has_search: bool,
    ) -> str:
        if search_mode:
            return "[dim]type to filter, ↑/↓: navigate, Enter: apply, Tab: preview, Esc: clear[/dim]"
        keys = ["↑/↓/k/j: navigate", "Enter: open", "/: search", "Tab: preview"]
        if preview_open:
            keys.append("Ctrl+P/N: scroll, gg/G: top/bottom")
        keys.append("e: editor")
        if total_pages > 1:
            keys.append("n/p: pages")
        keys.extend(["b: bookmark", "f: filter", "dd: delete"])
        if has_search:
            keys.append("Esc: clear search")
        keys.append("q: quit")
        return f"[dim]{', '.join(keys)}[/dim]"

    def _render_preview(
        self,
        chat: Optional[Chat],
        *,
        list_lines: int,
        scroll: int,
        view: dict[str, int],
    ) -> Group:
        """Render the bottom preview pane, windowed to the available height."""
        width, term_height = self.console.size
        # Reserve the list block + the rule separator; clamp to a usable band.
        height = max(
            PREVIEW_MIN_HEIGHT,
            min(PREVIEW_MAX_HEIGHT, term_height - list_lines - 2),
        )

        if chat is None:
            body: object = Text("(unreadable chat)", style="dim italic")
            view["total"], view["height"] = 0, height
            return Group(Rule(style="bright_black"), body)

        transcript = styled_transcript(chat)
        if not transcript.plain:
            view["total"], view["height"] = 0, height
            return Group(
                Rule(style="bright_black"), Text("(empty chat)", style="dim italic")
            )

        options = self.console.options.update(width=width, height=None)
        lines = self.console.render_lines(transcript, options, pad=False)
        total = len(lines)
        view["total"], view["height"] = total, height

        scroll = max(0, min(scroll, total - height))
        window = lines[scroll : scroll + height]
        segments: list[Segment] = []
        for line in window:
            segments.extend(line)
            segments.append(Segment.line())

        shown_end = min(scroll + height, total)
        title = f"{chat.metadata.title} — {scroll + 1}-{shown_end}/{total}"
        return Group(Rule(title, style="bright_black"), Segments(segments))

    def _open_in_editor(self, chat: Chat, live: Live) -> None:
        """Dump a cleaned transcript to a temp file and open it in $EDITOR."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", prefix="oi-chat-", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(plaintext_transcript(chat))
            path = handle.name

        live.stop()
        # stop() ends with a trailing newline, leaving the cursor one row below
        # the frame. Rich's next refresh erases upward assuming the cursor sits
        # on the frame's bottom line, so without this it misses the top line and
        # leaves a duplicate header. Step back up to realign.
        self.console.control(Control.move(0, -1))
        try:
            subprocess.run([*shlex.split(editor), path])
        finally:
            os.unlink(path)
            live.start(refresh=True)

    @staticmethod
    def _scroll_down(scroll: int, view: dict[str, int]) -> int:
        max_scroll = max(0, view["total"] - view["height"])
        return min(max_scroll, scroll + PREVIEW_SCROLL_LINES)

    def _select_prev(
        self,
        visible_chats: list[ChatMetadata],
        page_size: int,
        current_page: int,
        selected_index: int,
    ) -> tuple[int, int]:
        if selected_index > 0:
            return current_page, selected_index - 1
        if current_page > 0:
            current_page -= 1
            page = self._get_page_chats(visible_chats, current_page, page_size)
            return current_page, max(0, len(page) - 1)
        return current_page, selected_index

    def _select_next(
        self,
        visible_chats: list[ChatMetadata],
        page_size: int,
        current_page: int,
        selected_index: int,
        total_pages: int,
    ) -> tuple[int, int]:
        page = self._get_page_chats(visible_chats, current_page, page_size)
        if selected_index < len(page) - 1:
            return current_page, selected_index + 1
        if current_page < total_pages - 1:
            return current_page + 1, INITIAL_SELECTED_INDEX
        return current_page, selected_index

    def _get_page_chats(
        self, chats: list[ChatMetadata], current_page: int, page_size: int
    ) -> list[ChatMetadata]:
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, len(chats))
        return chats[start_idx:end_idx]

    def _filter_chats(
        self, chats: list[ChatMetadata], bookmarked_only: bool
    ) -> list[ChatMetadata]:
        """Filter chats for the current selector mode."""
        if not bookmarked_only:
            return chats
        return [chat for chat in chats if chat.bookmarked]

    def _clamp_selection_state(
        self,
        chats: list[ChatMetadata],
        page_size: int,
        current_page: int,
        selected_index: int,
    ) -> tuple[int, int, int]:
        """Clamp pagination + selected row after list changes."""
        total_pages = max(1, (len(chats) + page_size - 1) // page_size)
        current_page = min(max(current_page, 0), total_pages - 1)

        page_chats = self._get_page_chats(chats, current_page, page_size)
        if not page_chats:
            return current_page, INITIAL_SELECTED_INDEX, total_pages

        selected_index = min(max(selected_index, 0), len(page_chats) - 1)
        return current_page, selected_index, total_pages

    def _read_key(self) -> str:
        """Read one key from stdin across supported platforms."""
        if sys.platform == "win32":
            return self._read_key_windows()
        return self._read_key_unix()

    def _read_key_windows(self) -> str:
        """Read a single keypress using Windows msvcrt semantics."""
        import msvcrt

        read_wide = getattr(msvcrt, "getwch", None)
        read_byte = getattr(msvcrt, "getch")

        def read_key() -> str:
            if read_wide is not None:
                return read_wide()
            key_data = read_byte()
            if isinstance(key_data, bytes):
                return key_data.decode("utf-8", errors="ignore")
            return key_data

        key = read_key()
        if key in {"\x00", "\xe0"}:
            # Arrow/function keys emit a two-char sequence.
            key = read_key()
            if key == "H":
                return "\x1b[A"  # Up
            if key == "P":
                return "\x1b[B"  # Down
            return ""

        if key == "\x03":
            raise KeyboardInterrupt()

        return key

    def _read_key_unix(self) -> str:
        """Read a single keypress using Unix termios/tty raw mode."""
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            key = sys.stdin.read(1)

            # Distinguish a lone Esc from an escape sequence (arrow keys) by
            # peeking for follow-up bytes — a bare Esc has none, so reading a
            # fixed count would block until the next keypress.
            if key == "\x1b":
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    key += sys.stdin.read(1)
                    if key == "\x1b[" and select.select([sys.stdin], [], [], 0.05)[0]:
                        key += sys.stdin.read(1)

            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _refresh_chat_list(
        self, chats: list[ChatMetadata], deleted_id: str
    ) -> list[ChatMetadata]:
        """Remove deleted chat from the list."""
        return [chat for chat in chats if chat.id != deleted_id]

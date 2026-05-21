"""Interactive chat selection UI.

This module provides cross-platform keyboard input handling:
- Windows: Uses msvcrt for single-key input
- Unix/Linux/macOS: Uses termios/tty for raw terminal input
- Rich console output works consistently across all platforms
"""

import sys
from typing import Callable, Optional

from rich.console import Console
from rich.live import Live

from oi.constants import (
    DEFAULT_PAGE_SIZE,
    INITIAL_PAGE,
    INITIAL_SELECTED_INDEX,
    MAX_TITLE_LENGTH,
    NAVIGATION_KEYS,
)
from oi.core.session import Chat, ChatMetadata
from oi.ui.labels import INFO_LABEL, rich_message


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
        visible_chats = self._filter_chats(all_chats, bookmarked_only)
        current_page, selected_index, total_pages = self._clamp_selection_state(
            visible_chats, page_size, current_page, selected_index
        )

        def render_selection():
            visible_chats = self._filter_chats(all_chats, bookmarked_only)
            page_chats = self._get_page_chats(visible_chats, current_page, page_size)
            output = []
            scope = "bookmarked" if bookmarked_only else "all"

            output.append(
                "Select a chat to continue "
                f"({scope} chats, {current_page + 1}/{total_pages}):"
            )
            output.append("")

            if not visible_chats:
                output.append(
                    "[dim]No bookmarked chats. Press f to show all chats.[/dim]"
                )
                output.append("")
                output.append("[dim]f: toggle filter, q: quit[/dim]")
                return "\n".join(output)

            for i, chat in enumerate(page_chats):
                date_str = chat.updated_at.strftime("%Y-%m-%d %H:%M")
                title = (
                    chat.title[:MAX_TITLE_LENGTH] + "..."
                    if len(chat.title) > MAX_TITLE_LENGTH
                    else chat.title
                )
                bookmark_marker = "★" if chat.bookmarked else " "

                if i == selected_index:
                    # Highlighted selection
                    output.append(
                        f"[bright_yellow]❯ {i + 1:2}. {bookmark_marker} [{date_str}] {title:<78} "
                        f"[bright_black]({chat.model}, {chat.message_count} msgs)[/bright_black][/bright_yellow]"
                    )
                else:
                    # Normal entry
                    output.append(
                        f"  {i + 1:2}. {bookmark_marker} [{date_str}] {title:<78} "
                        f"[bright_black]({chat.model}, {chat.message_count} msgs)[/bright_black]"
                    )

            if total_pages > 1:
                output.append("")
                output.append(
                    "[dim]↑/↓/k/j or Ctrl+P/N: navigate, Enter: select, n/p or Ctrl+L/H: pages, b: bookmark, f: filter, dd: delete, q: quit[/dim]"
                )
            else:
                output.append("")
                output.append(
                    "[dim]↑/↓/k/j or Ctrl+P/N: navigate, Enter: select, b: bookmark, f: filter, dd: delete, q: quit[/dim]"
                )

            return "\n".join(output)

        try:
            with Live(
                render_selection(), console=self.console, refresh_per_second=10
            ) as live:
                while True:
                    visible_chats = self._filter_chats(all_chats, bookmarked_only)
                    page_chats = self._get_page_chats(
                        visible_chats, current_page, page_size
                    )
                    key = self._read_key()

                    if key in NAVIGATION_KEYS["UP"] and page_chats:
                        if selected_index > 0:
                            selected_index -= 1
                        elif current_page > 0:
                            current_page -= 1
                            selected_index = min(
                                len(
                                    self._get_page_chats(
                                        visible_chats, current_page, page_size
                                    )
                                )
                                - 1,
                                page_size - 1,
                            )

                    elif key in NAVIGATION_KEYS["DOWN"] and page_chats:
                        if selected_index < len(page_chats) - 1:
                            selected_index += 1
                        elif current_page < total_pages - 1:
                            current_page += 1
                            selected_index = INITIAL_SELECTED_INDEX

                    elif key in NAVIGATION_KEYS["ENTER"] and page_chats:
                        selected_chat = page_chats[selected_index]
                        loaded_chat = load_chat(selected_chat.id)
                        if loaded_chat is not None:
                            return loaded_chat

                        all_chats = self._refresh_chat_list(all_chats, selected_chat.id)
                        if not all_chats:
                            return None
                        current_page, selected_index, total_pages = (
                            self._clamp_selection_state(
                                self._filter_chats(all_chats, bookmarked_only),
                                page_size,
                                current_page,
                                selected_index,
                            )
                        )

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
                        selected_chat = page_chats[selected_index]
                        toggle_bookmark(selected_chat)
                        current_page, selected_index, total_pages = (
                            self._clamp_selection_state(
                                self._filter_chats(all_chats, bookmarked_only),
                                page_size,
                                current_page,
                                selected_index,
                            )
                        )

                    elif key == NAVIGATION_KEYS["FILTER_BOOKMARKED"]:
                        bookmarked_only = not bookmarked_only
                        current_page, selected_index, total_pages = (
                            self._clamp_selection_state(
                                self._filter_chats(all_chats, bookmarked_only),
                                page_size,
                                current_page,
                                selected_index,
                            )
                        )

                    elif key == NAVIGATION_KEYS["DELETE"]:
                        # Wait for second 'd'
                        second_key = self._read_key()
                        if second_key == NAVIGATION_KEYS["DELETE"] and page_chats:
                            # Delete the selected chat
                            selected_chat = page_chats[selected_index]
                            delete_chat(selected_chat.id)
                            # Refresh chat list and adjust selection
                            all_chats = self._refresh_chat_list(
                                all_chats, selected_chat.id
                            )
                            if not all_chats:
                                return None
                            current_page, selected_index, total_pages = (
                                self._clamp_selection_state(
                                    self._filter_chats(all_chats, bookmarked_only),
                                    page_size,
                                    current_page,
                                    selected_index,
                                )
                            )

                    elif key in NAVIGATION_KEYS["QUIT"]:
                        return None

                    # Update the display
                    live.update(render_selection())

        except KeyboardInterrupt:
            return None

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
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            key = sys.stdin.read(1)

            # Handle escape sequences (arrow keys)
            if key == "\x1b":
                key += sys.stdin.read(2)

            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _refresh_chat_list(
        self, chats: list[ChatMetadata], deleted_id: str
    ) -> list[ChatMetadata]:
        """Remove deleted chat from the list."""
        return [chat for chat in chats if chat.id != deleted_id]

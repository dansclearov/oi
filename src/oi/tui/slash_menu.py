"""Claude-Code-style slash-command menu shown above the input."""

from typing import Optional

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from oi.local_commands import LOCAL_COMMAND_SPECS, LocalCommandSpec


class SlashMenu(Vertical):
    """Command list that filters as a slash command is typed.

    The app drives it: `update_filter` with the current prefix (None hides),
    `move` for Ctrl+N/P navigation, `selected_name` for Tab completion.
    """

    DEFAULT_CSS = """
    SlashMenu {
        display: none;
        height: auto;
        padding: 0 2;
    }
    SlashMenu .menu-row {
        height: auto;
    }
    SlashMenu .menu-name {
        width: 14;
        color: ansi_bright_black;
    }
    SlashMenu .menu-desc {
        width: 1fr;
        color: ansi_bright_black;
    }
    SlashMenu .menu-row.-highlight .menu-name {
        color: ansi_bright_white;
        text-style: bold;
    }
    SlashMenu .menu-row.-highlight .menu-desc {
        color: ansi_default;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._matches: list[LocalCommandSpec] = []
        self._index = 0

    @property
    def is_open(self) -> bool:
        return self.display

    @property
    def selected_name(self) -> Optional[str]:
        if not self.display or not self._matches:
            return None
        return self._matches[self._index].name

    async def update_filter(self, prefix: Optional[str]) -> None:
        """Show the menu filtered by `prefix`, or hide it when None/no match."""
        if prefix is None:
            self._close()
            return

        matches = [spec for spec in LOCAL_COMMAND_SPECS if spec.name.startswith(prefix)]
        if not matches:
            self._close()
            return

        if matches != self._matches:
            self._matches = matches
            self._index = 0
            await self._rebuild()
        self.display = True

    def move(self, delta: int) -> None:
        if not self.display or not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self._apply_highlight()

    def _close(self) -> None:
        self.display = False
        self._matches = []
        self._index = 0

    async def _rebuild(self) -> None:
        await self.remove_children()
        rows = [
            Horizontal(
                Static(Text(spec.name), classes="menu-name"),
                Static(Text(spec.description), classes="menu-desc"),
                classes="menu-row",
            )
            for spec in self._matches
        ]
        await self.mount_all(rows)
        self._apply_highlight()

    def _apply_highlight(self) -> None:
        for i, row in enumerate(self.query(".menu-row")):
            row.set_class(i == self._index, "-highlight")

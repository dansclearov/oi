"""Paste support: clipboard images and large text pastes rendered as pills.

Both paste kinds occupy a single Unicode Private-Use codepoint in the input
buffer so backspace/vim motions treat the pill atomically. A prompt_toolkit
Processor expands each sentinel into a styled pill at display time.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_to_text
from prompt_toolkit.layout.processors import (
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.layout.utils import explode_text_fragments
from pydantic_ai.messages import BinaryContent

from oi.ui.labels import PILL_PT_STYLE

SENTINEL_BASE = 0xE000
SENTINEL_MAX = 0xF8FF
SUPPORTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


@dataclass
class _ImageEntry:
    content: BinaryContent


@dataclass
class _TextEntry:
    text: str
    line_count: int


_Entry = _ImageEntry | _TextEntry


@dataclass
class PasteStore:
    """Maps sentinel codepoints in the input buffer to paste payloads.

    Payloads are either images (become `BinaryContent` parts on submit) or
    text pastes (expanded back inline on submit).
    """

    _entries: dict[str, _Entry] = field(default_factory=dict)
    _counter: int = 0

    def _allocate_sentinel(self) -> str:
        codepoint = SENTINEL_BASE + self._counter
        if codepoint > SENTINEL_MAX:
            raise RuntimeError("Too many pastes; private-use area exhausted")
        self._counter += 1
        return chr(codepoint)

    def add_image(self, data: bytes, media_type: str) -> str:
        sentinel = self._allocate_sentinel()
        self._entries[sentinel] = _ImageEntry(
            BinaryContent(data=data, media_type=media_type)
        )
        return sentinel

    def add_text(self, text: str) -> str:
        sentinel = self._allocate_sentinel()
        line_count = text.count("\n") + 1
        self._entries[sentinel] = _TextEntry(text=text, line_count=line_count)
        return sentinel

    @property
    def has_entries(self) -> bool:
        return bool(self._entries)

    def is_sentinel(self, ch: str) -> bool:
        return ch in self._entries

    def pill_text(self, text: str) -> dict[str, str]:
        """Return sentinel → display pill string, numbered per kind by first occurrence."""
        image_n = 0
        paste_n = 0
        pills: dict[str, str] = {}
        for ch in text:
            if ch in pills:
                continue
            entry = self._entries.get(ch)
            if isinstance(entry, _ImageEntry):
                image_n += 1
                pills[ch] = f"[Image #{image_n}] "
            elif isinstance(entry, _TextEntry):
                paste_n += 1
                noun = "line" if entry.line_count == 1 else "lines"
                pills[ch] = f"[Paste #{paste_n} ({entry.line_count} {noun})] "
        return pills

    def split(self, text: str) -> list[str | BinaryContent]:
        """Split buffer text into alternating str / BinaryContent parts.

        Text-paste sentinels are expanded inline; image sentinels become
        `BinaryContent` parts. Consecutive text is merged.
        """
        parts: list[str | BinaryContent] = []
        buf: list[str] = []
        for ch in text:
            entry = self._entries.get(ch)
            if isinstance(entry, _ImageEntry):
                if buf:
                    parts.append("".join(buf))
                    buf = []
                parts.append(entry.content)
            elif isinstance(entry, _TextEntry):
                buf.append(entry.text)
            else:
                buf.append(ch)
        if buf:
            parts.append("".join(buf))
        return parts

    def reset(self) -> None:
        self._entries.clear()
        self._counter = 0


class PillProcessor(Processor):
    """Render paste sentinels as styled pills at display time only."""

    def __init__(self, store: PasteStore) -> None:
        self.store = store

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if not self.store.has_entries:
            return Transformation(ti.fragments)

        line_text = fragment_list_to_text(ti.fragments)
        if not any(self.store.is_sentinel(ch) for ch in line_text):
            return Transformation(ti.fragments)

        pills = self.store.pill_text(ti.document.text)
        exploded = explode_text_fragments(ti.fragments)

        new_fragments: StyleAndTextTuples = []
        sentinel_widths: dict[int, int] = {}

        for src_idx, fragment in enumerate(exploded):
            style = fragment[0]
            text = fragment[1]
            pill = pills.get(text)
            if pill is not None:
                new_fragments.append((PILL_PT_STYLE, pill))
                sentinel_widths[src_idx] = len(pill)
            else:
                new_fragments.append((style, text))

        line_len = len(exploded)

        def source_to_display(col: int) -> int:
            shift = sum(w - 1 for idx, w in sentinel_widths.items() if col > idx)
            return col + shift

        def display_to_source(col: int) -> int:
            src = 0
            disp = 0
            while src < line_len:
                width = sentinel_widths.get(src, 1)
                if disp + width > col:
                    return src
                disp += width
                src += 1
            return src

        return Transformation(
            new_fragments,
            source_to_display=source_to_display,
            display_to_source=display_to_source,
        )


def _run_capture(
    argv: list[str], timeout: float = 2.0
) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, check=False, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def read_clipboard_image() -> tuple[bytes, str] | None:
    """Return (image_bytes, media_type) from the system clipboard, or None."""
    if shutil.which("wl-paste"):
        listed = _run_capture(["wl-paste", "--list-types"])
        if listed and listed.returncode == 0:
            available = listed.stdout.decode("utf-8", errors="replace").splitlines()
            for mime in SUPPORTED_MEDIA_TYPES:
                if mime not in available:
                    continue
                got = _run_capture(["wl-paste", "-t", mime], timeout=5)
                if got and got.returncode == 0 and got.stdout:
                    return got.stdout, mime

    if shutil.which("xclip"):
        listed = _run_capture(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]
        )
        if listed and listed.returncode == 0:
            available = listed.stdout.decode("utf-8", errors="replace").splitlines()
            for mime in SUPPORTED_MEDIA_TYPES:
                if mime not in available:
                    continue
                got = _run_capture(
                    ["xclip", "-selection", "clipboard", "-t", mime, "-o"],
                    timeout=5,
                )
                if got and got.returncode == 0 and got.stdout:
                    return got.stdout, mime

    return None

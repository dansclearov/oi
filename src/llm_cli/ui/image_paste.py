"""Image paste support: clipboard reading, sentinel store, display pill processor."""

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

SENTINEL_BASE = 0xE000
SENTINEL_MAX = 0xF8FF
PILL_STYLE = "fg:ansicyan bold"
SUPPORTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


@dataclass
class ImagePasteStore:
    """Maps sentinel codepoints in the input buffer to BinaryContent images."""

    _images: dict[str, BinaryContent] = field(default_factory=dict)
    _counter: int = 0

    def add(self, data: bytes, media_type: str) -> str:
        """Allocate a unique sentinel char, store the image, and return the char."""
        codepoint = SENTINEL_BASE + self._counter
        if codepoint > SENTINEL_MAX:
            raise RuntimeError("Too many images pasted; private-use area exhausted")
        self._counter += 1
        sentinel = chr(codepoint)
        self._images[sentinel] = BinaryContent(data=data, media_type=media_type)
        return sentinel

    @property
    def has_images(self) -> bool:
        return bool(self._images)

    def is_sentinel(self, ch: str) -> bool:
        return ch in self._images

    def numbering(self, text: str) -> dict[str, int]:
        """Assign 1-based numbers to each sentinel by first occurrence in text."""
        numbering: dict[str, int] = {}
        for ch in text:
            if ch in self._images and ch not in numbering:
                numbering[ch] = len(numbering) + 1
        return numbering

    def split(self, text: str) -> list[str | BinaryContent]:
        """Split buffer text into alternating str / BinaryContent parts in order."""
        parts: list[str | BinaryContent] = []
        buf: list[str] = []
        for ch in text:
            image = self._images.get(ch)
            if image is not None:
                if buf:
                    parts.append("".join(buf))
                    buf = []
                parts.append(image)
            else:
                buf.append(ch)
        if buf:
            parts.append("".join(buf))
        return parts

    def reset(self) -> None:
        self._images.clear()
        self._counter = 0


class ImagePillProcessor(Processor):
    """Render image sentinels as styled `[Image N]` pills at display time only."""

    def __init__(self, store: ImagePasteStore) -> None:
        self.store = store

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if not self.store.has_images:
            return Transformation(ti.fragments)

        line_text = fragment_list_to_text(ti.fragments)
        if not any(self.store.is_sentinel(ch) for ch in line_text):
            return Transformation(ti.fragments)

        numbering = self.store.numbering(ti.document.text)
        exploded = explode_text_fragments(ti.fragments)

        new_fragments: StyleAndTextTuples = []
        sentinel_widths: dict[int, int] = {}

        for src_idx, fragment in enumerate(exploded):
            style = fragment[0]
            text = fragment[1]
            image_num = numbering.get(text)
            if image_num is not None:
                pill = f"[Image #{image_num}] "
                new_fragments.append((PILL_STYLE, pill))
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

"""Yomitan dictionary popups for text in the chat log (hover or click).

The lookups are Yomitan's own: the `yomitan-api` native-messaging host
(https://github.com/yomidevs/yomitan-api) serves the extension's term search
over HTTP on localhost, with the user's dictionaries, deinflection and
frequency data. oi sends the text from the pointer onward, the way the
browser popup scans from the cursor, and shows what comes back — deinflected
headwords, glossaries, frequencies, pitch accent — in a popup next to the
word. `originalTextLength` in the response is how many of the sent
characters the match covers, which is the span the popup belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Callable, Optional

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import events
from textual.containers import VerticalScroll
from textual.errors import NoWidget
from textual.geometry import Offset
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static, TextArea

YOMITAN_API_URL = "http://127.0.0.1:19633"
# Characters sent per lookup, from the pointer onward. Yomitan's default scan
# length; longer only matters for compounds past this length.
SCAN_LENGTH = 16
# Delay between the pointer settling on a word and the lookup (Yomitan's own
# scan delay is similar): a sweep across a line must not fire per character.
HOVER_DELAY = 0.2
REQUEST_TIMEOUT = 2.0
# Widest the popup body gets; entries wrap inside it. Height is capped by
# the room on the word's roomier side, so the popup never has to cover it.
POPUP_MAX_WIDTH = 60
POPUP_MAX_HEIGHT = 16
# After a failed connection, how long lookups are skipped before trying
# again — the host is down until the browser starts, not flapping.
RETRY_AFTER = 30.0

DIM = Style(color="bright_black")
BOLD = Style(bold=True)
INFLECTION = Style(color="bright_black", italic=True)


def is_lookup_char(char: str) -> bool:
    """Whether a character is worth a dictionary lookup: kana, kanji, hangul."""
    code = ord(char)
    return (
        0x3005 <= code <= 0x3007  # 々 〆 〇
        or 0x3040 <= code <= 0x30FF  # hiragana, katakana, ー
        or 0x3400 <= code <= 0x4DBF  # CJK extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF66 <= code <= 0xFF9F  # halfwidth katakana
        or 0xAC00 <= code <= 0xD7A3  # hangul syllables
    )


def cell_to_index(text: str, cell: int) -> int:
    """Index of the character covering terminal cell `cell` (double-width
    characters cover two); `len(text)` when the cell is past the text."""
    position = 0
    for index, char in enumerate(text):
        position += cell_len(char)
        if position > cell:
            return index
    return len(text)


def text_cells(text: str) -> int:
    return sum(cell_len(char) for char in text)


# --- Yomitan's response ------------------------------------------------------


@dataclass
class Definition:
    dictionary: str
    tags: list[str]
    lines: list[Text]


@dataclass
class Entry:
    headwords: list[tuple[str, str]]
    """(term, reading) pairs, the primary one first."""
    tags: list[str]
    inflections: list[str]
    frequencies: list[str]
    pitches: list[int]
    definitions: list[Definition] = field(default_factory=list)


@dataclass
class LookupResult:
    length: int
    """How many characters of the sent text the match covers."""
    entries: list[Entry]


_SKIP_TAGS = {"img", "rt"}  # rt: furigana; the reading is in the header already
# Jitendex `data-content` sections that are noise in a one-glance popup.
_SKIP_CONTENT = {"attribution", "attribution-footnote", "example-sentence"}
# Jitendex asides laid out as a small label block over a text block
# (`Note` / `occ. 奔る`); one line reads better in a popup.
_LABELLED_CONTENT = {"sense-note", "lang-source"}
_BLOCK_TAGS = {"div", "details", "summary", "table", "thead", "tbody", "tr"}
_SMALL_FONT_SIZES = {"small", "x-small", "xx-small", "smaller"}


def _bullet(list_style: Optional[str]) -> str:
    if list_style is None:
        return "• "
    value = list_style.strip()
    if value.startswith('"') or value.startswith("'"):
        marker = value.strip("\"'")
        if len(marker) == 1 and 0x2460 <= ord(marker) <= 0x2473:
            # ①…⑳ (Jitendex's sense numbers) are ambiguous-width: terminals
            # with a CJK fallback font draw them two cells wide over the
            # space that follows, so they read as `1.` instead.
            return f"{ord(marker) - 0x2460 + 1}. "
        return marker + " "
    if value == "none":
        return ""
    return "• "


def _list_marker(tag: str, style: Optional[dict]) -> Callable[[], str]:
    if tag == "ol":
        counter = [0]

        def numbered() -> str:
            counter[0] += 1
            return f"{counter[0]}. "

        return numbered
    bullet = _bullet((style or {}).get("listStyleType"))
    return lambda: bullet


def _inline_items(node: dict) -> Optional[list[str]]:
    """The item texts of a list whose items are all plain strings, else None."""
    content = node.get("content")
    items = content if isinstance(content, list) else [content]
    texts = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            return None
        texts.append(item["content"])
    return texts


def _muted(style: dict) -> bool:
    """Small print and tag chips (a colored box behind the text) are
    secondary in the dictionary's own layout; dim stands in for both."""
    if "backgroundColor" in style:
        return True
    size = str(style.get("fontSize", "")).strip()
    if size in _SMALL_FONT_SIZES:
        return True
    if size.endswith("em"):
        try:
            return float(size[:-2]) < 1
        except ValueError:
            return False
    return False


_Chunks = list[tuple[str, Optional[Style]]]


def _join(chunks: _Chunks) -> Text:
    """One line from styled chunks, whitespace collapsed like HTML does."""
    text = Text()
    after_space = True  # nothing yet: leading whitespace is dropped
    for chunk, style in chunks:
        chunk = _WHITESPACE_RE.sub(" ", chunk)
        if after_space and chunk.startswith(" "):
            chunk = chunk[1:]
        if not chunk:
            continue
        text.append(chunk, style)
        after_space = chunk.endswith(" ")
    text.rstrip()
    return text


_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


def flatten_content(node: Any) -> list[Text]:
    """Render Yomitan structured content (an HTML-like JSON tree) as lines.

    Lists nest by indentation with their own markers; a `ul` of plain
    strings is joined inline (Jitendex's glossary lists read as `a; b; c`);
    tables become aligned columns; ruby shows the base text only; images and
    attribution are dropped; small print and tag chips are dimmed.
    """
    lines: list[Text] = []
    buffer: _Chunks = []
    indent = [""]

    def flush() -> None:
        line = _join(buffer)
        buffer.clear()
        if line.plain:
            lines.append(Text(indent[0]).append_text(line))

    def inline_text(content: Any, style: Optional[Style]) -> Text:
        saved = buffer[:]
        buffer.clear()
        walk(content, 0, None, style, True)
        text = _join(buffer)
        buffer[:] = saved
        return text

    def table(node: dict, style: Optional[Style]) -> None:
        rows: list[list[tuple[bool, Text]]] = []

        def collect(item: Any) -> None:
            if isinstance(item, list):
                for child in item:
                    collect(child)
            elif isinstance(item, dict):
                if item.get("tag") != "tr":
                    collect(item.get("content"))
                    return
                content = item.get("content")
                cells = content if isinstance(content, list) else [content]
                rows.append(
                    [
                        (cell["tag"] == "th", inline_text(cell.get("content"), style))
                        for cell in cells
                        if isinstance(cell, dict) and cell.get("tag") in ("th", "td")
                    ]
                )

        collect(node.get("content"))
        widths: list[int] = []
        for row in rows:
            for column, (_, text) in enumerate(row):
                if column == len(widths):
                    widths.append(0)
                widths[column] = max(widths[column], text_cells(text.plain))
        for row in rows:
            line = Text()
            for column, (header, text) in enumerate(row):
                if header:
                    text.stylize(BOLD)
                line.append_text(text)
                line.append(" " * (widths[column] - text_cells(text.plain) + 2))
            line.rstrip()
            if line.plain:
                lines.append(Text(indent[0]).append_text(line))

    def walk(
        node: Any,
        depth: int,
        marker: Optional[Callable[[], str]],
        style: Optional[Style],
        inline: bool,
    ) -> None:
        """`inline`: block structure is flattened into the current line
        (table cells, labelled asides)."""
        if isinstance(node, str):
            if inline:
                buffer.append((node.replace("\n", " "), style))
                return
            for i, part in enumerate(node.split("\n")):
                if i:
                    flush()
                buffer.append((part, style))
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth, marker, style, inline)
            return
        if not isinstance(node, dict):
            return
        tag = node.get("tag")
        content = node.get("content")
        data = node.get("data") or {}
        attrs = node.get("style") or {}
        if tag in _SKIP_TAGS or data.get("content") in _SKIP_CONTENT:
            return
        if _muted(attrs):
            style = DIM
        if tag == "br":
            if inline:
                buffer.append((" ", None))
            else:
                flush()
            return
        block = tag in _BLOCK_TAGS or tag in ("ul", "ol", "li")
        if inline:
            if block:
                buffer.append((" ", None))
            walk(content, depth, marker, style, True)
            if block or "marginRight" in attrs:
                buffer.append((" ", None))
            return
        if tag == "table":
            flush()
            table(node, style)
            return
        if tag in ("ul", "ol"):
            items = _inline_items(node) if tag == "ul" else None
            if items is not None:
                separator = "; " if data.get("content") == "glossary" else ", "
                buffer.append((separator.join(items), style))
                return
            flush()
            walk(content, depth + 1, _list_marker(tag, attrs), style, False)
            flush()
            return
        if tag == "li":
            flush()
            indent[0] = "  " * max(depth - 1, 0)
            if "listStyleType" in attrs:
                buffer.append((_bullet(attrs["listStyleType"]), None))
            elif marker is not None:
                buffer.append((marker(), None))
            walk(content, depth, None, style, False)
            flush()
            return
        if data.get("content") in _LABELLED_CONTENT:
            flush()
            walk(content, depth, marker, style, True)
            flush()
            return
        if block:
            flush()
            walk(content, depth, marker, style, False)
            flush()
            return
        walk(content, depth, marker, style, False)
        if "marginRight" in attrs:
            # Jitendex's tag chips space themselves with margins.
            buffer.append((" ", None))

    walk(node, 0, None, None, False)
    flush()
    return lines


def _glossary_lines(entries: Any) -> list[Text]:
    plain: list[str] = []
    lines: list[Text] = []
    for item in entries if isinstance(entries, list) else []:
        if isinstance(item, str):
            plain.append(item)
        elif isinstance(item, dict):
            if item.get("type") == "text":
                plain.append(str(item.get("text", "")))
            elif item.get("type") == "structured-content":
                lines.extend(flatten_content(item.get("content")))
    plain_lines: list[Text] = []
    for n, gloss in enumerate(plain, 1):
        for i, part in enumerate(gloss.split("\n")):
            prefix = f"{n}. " if len(plain) > 1 and i == 0 else ""
            plain_lines.append(Text(prefix + part))
    return plain_lines + lines


def _tag_names(tags: Any) -> list[str]:
    names = []
    for tag in tags if isinstance(tags, list) else []:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _parse_entry(raw: dict) -> Entry:
    headwords = [
        (str(h.get("term", "")), str(h.get("reading", "")))
        for h in raw.get("headwords") or []
    ]
    first_headword = (raw.get("headwords") or [{}])[0]

    inflections: list[str] = []
    for candidate in raw.get("inflectionRuleChainCandidates") or []:
        rules = candidate.get("inflectionRules") or []
        if rules:
            inflections = _tag_names(rules)
            break

    frequencies: list[str] = []
    seen_dictionaries: set[str] = set()
    for item in raw.get("frequencies") or []:
        dictionary = str(item.get("dictionary", ""))
        if dictionary in seen_dictionaries:
            continue
        seen_dictionaries.add(dictionary)
        value = item.get("displayValue")
        if value is None:
            value = item.get("frequency")
        if value is not None:
            frequencies.append(str(value))

    pitches: list[int] = []
    for item in raw.get("pronunciations") or []:
        for pronunciation in item.get("pronunciations") or []:
            position = pronunciation.get("positions")
            if (
                pronunciation.get("type") == "pitch-accent"
                and isinstance(position, int)
                and position not in pitches
            ):
                pitches.append(position)

    definitions = [
        Definition(
            dictionary=str(d.get("dictionary", "")),
            tags=_tag_names(d.get("tags")),
            lines=_glossary_lines(d.get("entries")),
        )
        for d in raw.get("definitions") or []
    ]
    return Entry(
        headwords=headwords,
        tags=_tag_names(first_headword.get("tags")),
        inflections=inflections,
        frequencies=frequencies,
        pitches=pitches,
        definitions=definitions,
    )


def parse_term_entries(payload: dict) -> LookupResult:
    """Distill a `/termEntries` response into what the popup shows."""
    entries = [
        _parse_entry(raw)
        for raw in payload.get("dictionaryEntries") or []
        if isinstance(raw, dict)
    ]
    length = payload.get("originalTextLength")
    return LookupResult(
        length=length if isinstance(length, int) else 0, entries=entries
    )


def render_entries(entries: list[Entry]) -> Text:
    """The popup body: one block per entry, Yomitan's layout in plain text."""
    text = Text()
    for n, entry in enumerate(entries):
        if n:
            text.append("\n\n")
        for i, (term, reading) in enumerate(entry.headwords):
            if i:
                text.append(" · ", DIM)
            text.append(term, BOLD if i == 0 else DIM)
            if reading and reading != term:
                text.append(f"【{reading}】", None if i == 0 else DIM)
        for pitch in entry.pitches:
            text.append(f" [{pitch}]", DIM)
        if entry.tags:
            text.append("  " + " ".join(entry.tags), DIM)
        if entry.frequencies:
            text.append("  " + " · ".join(entry.frequencies), DIM)
        if entry.inflections:
            text.append("\n" + " « ".join(entry.inflections), INFLECTION)
        for definition in entry.definitions:
            text.append("\n" + definition.dictionary, DIM)
            if definition.tags:
                text.append("  " + " ".join(definition.tags), DIM)
            for line in definition.lines:
                text.append("\n  ").append_text(line)
    return text


class YomitanClient:
    """`/termEntries` over HTTP, remembering when the host isn't there."""

    def __init__(self, base_url: str = YOMITAN_API_URL) -> None:
        self.base_url = base_url
        self._retry_at = 0.0

    @property
    def reachable(self) -> bool:
        return monotonic() >= self._retry_at

    async def term_entries(self, text: str) -> Optional[LookupResult]:
        """Entries for `text`, or None when the host can't be reached."""
        if not self.reachable:
            return None
        import httpx

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{self.base_url}/termEntries", json={"term": text}
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            self._retry_at = monotonic() + RETRY_AFTER
            return None
        return parse_term_entries(payload if isinstance(payload, dict) else {})


# --- text under the pointer --------------------------------------------------


@dataclass
class LookupTarget:
    widget: Widget
    line: int
    """Line within the widget's rendered content."""
    cell: int
    """Cell within that line where the character under the pointer starts."""
    text: str
    """What to send: from the pointer to the end of the line and on into the
    next (a word can wrap), capped at `SCAN_LENGTH`."""
    line_chars: int
    """Characters left on the pointer's line, so a match that wraps is
    highlighted on that line only."""
    screen_offset: Offset
    """Screen position of the character: where the popup anchors."""


def locate(scope: Widget, x: int, y: int) -> Optional[tuple[Widget, int, int]]:
    """The text-bearing widget under screen cell (x, y) inside `scope`, with
    the cell's position in that widget's rendered content."""
    try:
        widget, _ = scope.screen.get_widget_at(x, y)
    except NoWidget:
        return None
    if scope not in widget.ancestors or isinstance(widget, TextArea):
        return None
    region = widget.content_region
    scroll = widget.scroll_offset
    rel_x, rel_y = x - region.x + scroll.x, y - region.y + scroll.y
    if rel_x < 0 or rel_y < 0:
        return None
    return widget, rel_x, rel_y


def resolve_target(scope: Widget, x: int, y: int) -> Optional[LookupTarget]:
    """What to look up for the pointer at screen cell (x, y), if anything."""
    located = locate(scope, x, y)
    if located is None:
        return None
    widget, rel_x, rel_y = located
    line = widget.render_line(rel_y).text
    index = cell_to_index(line, rel_x)
    if index >= len(line) or not is_lookup_char(line[index]):
        return None
    rest = line[index:].rstrip()
    scan = rest
    if len(scan) < SCAN_LENGTH:
        scan += widget.render_line(rel_y + 1).text.strip()
    cell = text_cells(line[:index])
    return LookupTarget(
        widget=widget,
        line=rel_y,
        cell=cell,
        text=scan[:SCAN_LENGTH],
        line_chars=len(rest),
        screen_offset=Offset(
            widget.content_region.x + cell - widget.scroll_offset.x, y
        ),
    )


@dataclass
class _Span:
    """The looked-up word's cells: the popup stays while the pointer is on it."""

    widget: Widget
    line: int
    start: int
    end: int

    def contains(self, widget: Widget, x: int, y: int) -> bool:
        return widget is self.widget and y == self.line and self.start <= x < self.end


class LookupPopup(VerticalScroll):
    """The dictionary popup, plus the hover/click logic that drives it.

    The app forwards pointer events; the popup resolves the word under the
    pointer within `scope` (the chat log), asks Yomitan, and anchors itself
    one row below the word (`margin` + `constrain: inflect` flip it above
    when there's no room below, like the browser popup). It hides when the
    pointer leaves the word, unless it moves onto the popup itself — so a
    long entry can be scrolled with the wheel.
    """

    DEFAULT_CSS = """
    LookupPopup {
        layer: lookup;
        display: none;
        /* Width is set per lookup (`_fetch`): auto would size to the
           longest line and clip it instead of wrapping the body. */
        height: auto;
        margin: 1 0;
        padding: 0 1;
        border: round ansi_bright_black;
        background: ansi_default;
        constrain: inside inflect;
        scrollbar-size-vertical: 0;
    }
    LookupPopup > Static {
        width: 100%;
        height: auto;
    }
    """

    class Unreachable(Message):
        """A click asked for a lookup but the Yomitan API isn't listening."""

        def __init__(self, url: str) -> None:
            super().__init__()
            self.url = url

    def __init__(self, client: YomitanClient, *, hover: bool) -> None:
        super().__init__(can_focus=False)
        self.client = client
        self.hover = hover
        self.scope: Optional[Widget] = None
        self._span: Optional[_Span] = None
        self._timer: Optional[Timer] = None

    def compose(self):
        yield Static(Text(), classes="lookup-body")

    def hide(self) -> None:
        self._cancel_pending()
        self._span = None
        if self.display:
            self.display = False

    def _cancel_pending(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.workers.cancel_group(self, "lookup")

    def _over_popup(self, x: int, y: int) -> bool:
        return self.display and self.region.contains(x, y)

    def pointer_moved(self, event: events.MouseMove) -> None:
        if event.button or self.scope is None:
            # A drag is a text selection, not a hover.
            return
        x, y = event.screen_x, event.screen_y
        if self._over_popup(x, y):
            return
        if self._span is not None:
            located = locate(self.scope, x, y)
            if located is not None and self._span.contains(*located):
                return
            self.hide()
        if not self.hover:
            return
        self._cancel_pending()
        self._timer = self.set_timer(HOVER_DELAY, lambda: self._lookup(x, y))

    def pointer_clicked(self, event: events.Click) -> None:
        if event.button != 1 or event.chain != 1 or self.scope is None:
            return
        x, y = event.screen_x, event.screen_y
        if self._over_popup(x, y):
            return
        self._cancel_pending()
        self._lookup(x, y, notify=True)

    def _lookup(self, x: int, y: int, *, notify: bool = False) -> None:
        assert self.scope is not None
        target = resolve_target(self.scope, x, y)
        if target is None:
            return
        if not self.client.reachable:
            if notify:
                self.post_message(self.Unreachable(self.client.base_url))
            return
        self.run_worker(self._fetch(target, notify), group="lookup", exclusive=True)

    async def _fetch(self, target: LookupTarget, notify: bool) -> None:
        result = await self.client.term_entries(target.text)
        if result is None:
            if notify:
                self.post_message(self.Unreachable(self.client.base_url))
            self.hide()
            return
        if not result.entries:
            self.hide()
            return
        matched = target.text[: min(result.length, target.line_chars)]
        self._span = _Span(
            target.widget,
            target.line,
            target.cell,
            target.cell + max(text_cells(matched), 1),
        )
        body = render_entries(result.entries)
        longest = max((text_cells(line) for line in body.plain.splitlines()), default=1)
        # Border and padding take two cells a side, a row above and below.
        screen = self.screen.size
        self.styles.width = max(min(longest, POPUP_MAX_WIDTH, screen.width - 4), 1) + 4
        word_y = target.screen_offset.y
        room = max(screen.height - word_y - 2, word_y - 1)
        self.styles.max_height = max(min(room, POPUP_MAX_HEIGHT), 4)
        self.query_one(Static).update(body)
        self.absolute_offset = target.screen_offset
        self.scroll_home(animate=False)
        self.display = True

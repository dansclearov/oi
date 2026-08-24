"""Modal vim emulation for the TUI input.

A key-dispatch layer over Textual's `TextArea` primitives (document
index/location conversion, selection, edit methods, undo stack). Motions and
text objects are pure functions over `(text, index)` so they can be tested
without a widget.

Supported: normal/visual/visual-line modes; `h j k l w b e 0 ^ $ gg G`
motions with counts; `f F t T ; ,` finds; `i a I A o O` insert entries;
`x X r s S D C ~` edits; `d c y` operators composed with motions, doubled
(`dd cc yy`), or with `i`/`a` text objects (`w " ' ` ( ) b [ ] { } B`);
`p P` with one internal register (charwise/linewise); `u`/`Ctrl+R` undo/redo;
`v V` visual with `o` end-swap and operators/objects on the selection.
"""

from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional, cast

if TYPE_CHECKING:
    from textual.document._document import Document
    from textual.widgets import TextArea


class Mode(Enum):
    INSERT = "insert"
    NORMAL = "normal"
    VISUAL = "visual"
    VISUAL_LINE = "visual_line"


_SPACE, _WORD, _PUNCT = 0, 1, 2

_PAIR_ALIASES = {
    "(": ("(", ")"),
    ")": ("(", ")"),
    "b": ("(", ")"),
    "[": ("[", "]"),
    "]": ("[", "]"),
    "{": ("{", "}"),
    "}": ("{", "}"),
    "B": ("{", "}"),
}
_QUOTES = {'"', "'", "`"}


def _cls(ch: str) -> int:
    if ch.isspace():
        return _SPACE
    if ch.isalnum() or ch == "_":
        return _WORD
    return _PUNCT


def next_word_start(text: str, idx: int) -> int:
    """`w`: start of the next word (newlines count as whitespace)."""
    n = len(text)
    if idx >= n:
        return n
    cls = _cls(text[idx])
    if cls != _SPACE:
        while idx < n and _cls(text[idx]) == cls:
            idx += 1
    while idx < n and _cls(text[idx]) == _SPACE:
        idx += 1
    return idx


def prev_word_start(text: str, idx: int) -> int:
    """`b`: start of the previous word."""
    idx -= 1
    while idx >= 0 and _cls(text[idx]) == _SPACE:
        idx -= 1
    if idx < 0:
        return 0
    cls = _cls(text[idx])
    while idx > 0 and _cls(text[idx - 1]) == cls:
        idx -= 1
    return idx


def word_end(text: str, idx: int) -> int:
    """`e`: end of the current/next word (inclusive index)."""
    n = len(text)
    if n == 0:
        return 0
    idx += 1
    while idx < n and _cls(text[idx]) == _SPACE:
        idx += 1
    if idx >= n:
        return n - 1
    cls = _cls(text[idx])
    while idx + 1 < n and _cls(text[idx + 1]) == cls:
        idx += 1
    return idx


def line_bounds(text: str, idx: int) -> tuple[int, int]:
    """(start, end) of the line containing idx; end excludes the newline."""
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    if end == -1:
        end = len(text)
    return start, end


def first_non_blank(text: str, idx: int) -> int:
    start, end = line_bounds(text, idx)
    i = start
    while i < end and text[i] in " \t":
        i += 1
    return i


def find_char(text: str, idx: int, char: str, kind: str) -> Optional[int]:
    """`f F t T` targets, bounded to the current line."""
    start, end = line_bounds(text, idx)
    if kind in ("f", "t"):
        found = text.find(char, idx + 1, end)
        if found == -1:
            return None
        return found - 1 if kind == "t" else found
    found = text.rfind(char, start, idx)
    if found == -1:
        return None
    return found + 1 if kind == "T" else found


def _match_pair(
    text: str, idx: int, open_c: str, close_c: str
) -> Optional[tuple[int, int]]:
    n = len(text)
    if idx < n and text[idx] == close_c:
        close_i = idx
        search_from = idx - 1
    else:
        close_i = None
        search_from = idx

    depth = 0
    j = search_from
    while j >= 0:
        ch = text[j]
        if ch == close_c and j != idx:
            depth += 1
        elif ch == open_c:
            if depth == 0:
                break
            depth -= 1
        j -= 1
    else:
        return None
    open_i = j

    if close_i is not None:
        return open_i, close_i

    depth = 0
    k = open_i + 1
    while k < n:
        ch = text[k]
        if ch == open_c:
            depth += 1
        elif ch == close_c:
            if depth == 0:
                # The cursor must be within the pair (vim fails outside it).
                return (open_i, k) if k >= idx else None
            depth -= 1
        k += 1
    return None


def _quote_range(text: str, idx: int, quote: str) -> Optional[tuple[int, int]]:
    start, end = line_bounds(text, idx)
    positions = [i for i in range(start, end) if text[i] == quote]
    pairs = list(zip(positions[::2], positions[1::2]))
    for open_i, close_i in pairs:
        if idx <= close_i:
            return open_i, close_i
    return None


def object_range(
    text: str, idx: int, kind: str, inner: bool
) -> Optional[tuple[int, int]]:
    """Range [start, end) for a text object (`iw`, `a"`, `i(` …)."""
    n = len(text)
    if kind == "w":
        if n == 0:
            return None
        idx = min(idx, n - 1)
        cls = _cls(text[idx])
        start = idx
        while start > 0 and _cls(text[start - 1]) == cls:
            start -= 1
        end = idx + 1
        while end < n and _cls(text[end]) == cls:
            end += 1
        if inner or cls == _SPACE:
            return start, end
        # `aw`: trailing whitespace, or leading when there is none.
        after = end
        while after < n and text[after] in " \t":
            after += 1
        if after > end:
            return start, after
        before = start
        while before > 0 and text[before - 1] in " \t":
            before -= 1
        return before, end

    if kind in _QUOTES:
        found = _quote_range(text, idx, kind)
        if found is None:
            return None
        open_i, close_i = found
        return (open_i + 1, close_i) if inner else (open_i, close_i + 1)

    if kind in _PAIR_ALIASES:
        open_c, close_c = _PAIR_ALIASES[kind]
        found = _match_pair(text, idx, open_c, close_c)
        if found is None:
            return None
        open_i, close_i = found
        return (open_i + 1, close_i) if inner else (open_i, close_i + 1)

    return None


class VimHandler:
    """Per-input modal state machine; the ChatInput delegates keys here."""

    def __init__(
        self,
        text_area: "TextArea",
        on_mode_change: Optional[Callable[[Mode], None]] = None,
        atom_spans: Optional[Callable[[], list[tuple[int, int]]]] = None,
    ) -> None:
        self.ta = text_area
        self._mode = Mode.INSERT
        self.on_mode_change = on_mode_change
        # Ranges that behave as one character: the cursor never lands inside
        # one, motions step over them, and edits take them whole.
        self.atom_spans = atom_spans
        self._count = ""
        self._operator: Optional[str] = None
        self._pending: Optional[str] = (
            None  # "r" | "f" | "F" | "t" | "T" | "g" | "iobj" | "aobj"
        )
        self._register = ""
        self._register_linewise = False
        self._last_find: Optional[tuple[str, str]] = None

    # ------------------------------------------------------------- plumbing

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        changed = value is not self._mode
        self._mode = value
        if changed and self.on_mode_change is not None:
            self.on_mode_change(value)

    @property
    def _text(self) -> str:
        return self.ta.text

    @property
    def _document(self) -> "Document":
        # TextArea types this as the abstract DocumentBase; the concrete
        # Document (with index<->location conversion) is what it always holds.
        return cast("Document", self.ta.document)

    @property
    def _idx(self) -> int:
        return self._document.get_index_from_location(self.ta.cursor_location)

    def _loc(self, idx: int):
        return self._document.get_location_from_index(max(0, min(idx, len(self._text))))

    # -------------------------------------------------------------- atoms

    def _atoms(self) -> list[tuple[int, int]]:
        return self.atom_spans() if self.atom_spans is not None else []

    def _snap(self, idx: int, *, forward: bool) -> int:
        """Push an index out of any atom it landed inside."""
        for start, end in self._atoms():
            if start < idx < end:
                return end if forward else start
        return idx

    def _expand_range(self, start: int, end: int) -> tuple[int, int]:
        """Grow an edit range to fully cover any atom it clips."""
        for atom_start, atom_end in self._atoms():
            if atom_start < end and start < atom_end:
                start = min(start, atom_start)
                end = max(end, atom_end)
        return start, end

    def _atom_at(self, idx: int) -> Optional[tuple[int, int]]:
        """The atom starting at or containing idx, if any."""
        for start, end in self._atoms():
            if start <= idx < end:
                return start, end
        return None

    def _move(self, idx: int, *, select: Optional[bool] = None) -> None:
        if select is None:
            select = self.mode in (Mode.VISUAL, Mode.VISUAL_LINE)
        idx = self._snap(idx, forward=idx >= self._idx)
        self.ta.move_cursor(self._loc(idx), select=select)
        if self.mode is Mode.VISUAL_LINE:
            self._expand_visual_line()

    def _take_count(self) -> int:
        count = int(self._count) if self._count else 1
        self._count = ""
        return count

    def _clear_pending(self) -> None:
        self._count = ""
        self._operator = None
        self._pending = None

    def enter_insert(self) -> None:
        self.mode = Mode.INSERT
        self._clear_pending()

    def enter_normal(self) -> None:
        self.mode = Mode.NORMAL
        self._clear_pending()
        self.ta.selection = self.ta.selection.cursor(self.ta.cursor_location)

    # --------------------------------------------------------------- escape

    def handle_escape(self) -> bool:
        """Esc pressed (menu closed). True when consumed by the vim layer."""
        if self.mode is Mode.INSERT:
            self.enter_normal()
            # Like vim: leaving insert steps the cursor back one column.
            idx = self._idx
            start, _ = line_bounds(self._text, idx)
            if idx > start:
                self._move(idx - 1, select=False)
            return True
        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
            self.enter_normal()
            return True
        if self._count or self._operator or self._pending:
            self._clear_pending()
            return True
        return False

    # ----------------------------------------------------------------- keys

    # Non-printable keys with a vim meaning outside insert mode.
    KEY_ALIASES = {"backspace": "h", "delete": "x"}

    def handle_key(self, event) -> None:
        """Handle one key in normal/visual mode (caller filters INSERT)."""
        if event.key == "ctrl+r":
            self.ta.redo()
            self._clear_pending()
            return
        if event.key in self.KEY_ALIASES:
            char = self.KEY_ALIASES[event.key]
        elif not event.is_printable or event.character is None:
            return
        else:
            char = event.character

        if self._pending is not None:
            self._handle_pending_char(char)
            return

        if char.isdigit() and (char != "0" or self._count):
            self._count += char
            return

        if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
            if self._handle_visual_key(char):
                return
        self._handle_normal_key(char)

    # ------------------------------------------------------------- pendings

    def _handle_pending_char(self, char: str) -> None:
        pending = self._pending
        self._pending = None
        text, idx = self._text, self._idx

        if pending == "g":
            if char == "g":
                self._motion_target("gg")
            else:
                self._clear_pending()
            return

        if pending == "r":
            count = self._take_count()
            _, line_end = line_bounds(text, idx)
            if idx + count <= line_end:
                start, end = self._expand_range(idx, idx + count)
                self.ta.replace(char * count, self._loc(start), self._loc(end))
                self._move(start + count - 1)
            return

        if pending in ("f", "F", "t", "T"):
            self._last_find = (char, pending)
            self._apply_find(char, pending)
            return

        if pending in ("iobj", "aobj"):
            inner = pending == "iobj"
            found = object_range(text, idx, char, inner)
            if found is None:
                self._clear_pending()
                return
            start, end = found
            if self.mode in (Mode.VISUAL, Mode.VISUAL_LINE):
                self.mode = Mode.VISUAL
                self.ta.selection = self.ta.selection.__class__(
                    self._loc(start), self._loc(end)
                )
                return
            operator = self._operator
            self._operator = None
            if operator is not None:
                self._apply_operator(operator, start, end, linewise=False)
            return

        self._clear_pending()

    def _apply_find(self, char: str, kind: str) -> None:
        count = self._take_count()
        idx = self._idx
        for _ in range(count):
            found = find_char(self._text, idx, char, kind)
            if found is None:
                self._operator = None
                return
            idx = found
        inclusive = kind in ("f", "t")
        self._finish_motion(idx, inclusive=inclusive)

    # -------------------------------------------------------------- motions

    def _motion_target(self, motion: str) -> None:
        """Resolve a motion key to a target index and finish it."""
        text, idx = self._text, self._idx
        count = self._take_count()
        inclusive = False
        linewise = False
        target = idx

        if motion == "h":
            start, _ = line_bounds(text, idx)
            target = max(idx - count, start)
        elif motion == "l":
            _, end = line_bounds(text, idx)
            target = min(idx + count, max(end - 1, 0)) if end > 0 else idx
            if self._operator is not None or self.mode is Mode.VISUAL:
                target = min(idx + count, end)
                # As an operator range `l` is exclusive of the target itself.
                self._finish_motion(target, inclusive=False)
                return
        elif motion in ("j", "k"):
            row, col = self.ta.cursor_location
            new_row = row + count if motion == "j" else row - count
            new_row = max(0, min(new_row, self.ta.document.line_count - 1))
            if self._operator is not None:
                start_row, end_row = sorted((row, new_row))
                self._operate_lines(start_row, end_row)
                return
            line_len = len(self.ta.document.get_line(new_row))
            self.ta.move_cursor(
                (new_row, min(col, line_len)),
                select=self.mode in (Mode.VISUAL, Mode.VISUAL_LINE),
            )
            if self.mode is Mode.VISUAL_LINE:
                self._expand_visual_line()
            return
        elif motion == "w":
            for _ in range(count):
                target = next_word_start(text, target)
        elif motion == "b":
            for _ in range(count):
                target = prev_word_start(text, target)
        elif motion == "e":
            for _ in range(count):
                target = word_end(text, target)
            inclusive = True
        elif motion == "0":
            target, _ = line_bounds(text, idx)
        elif motion == "^":
            target = first_non_blank(text, idx)
        elif motion == "$":
            _, end = line_bounds(text, idx)
            target = max(end - 1, 0)
            inclusive = True
        elif motion == "G":
            linewise = True
            target = len(text)
        elif motion == "gg":
            linewise = True
            target = 0

        if linewise and self._operator is not None:
            row = self._loc(target)[0]
            self._operate_lines(*sorted((self.ta.cursor_location[0], row)))
            return
        if linewise:
            target = first_non_blank(
                text, target if target < len(text) else max(len(text) - 1, 0)
            )

        self._finish_motion(target, inclusive=inclusive)

    def _finish_motion(self, target: int, *, inclusive: bool) -> None:
        operator = self._operator
        self._operator = None
        if operator is None:
            self._move(target)
            return

        idx = self._idx
        start, end = sorted((idx, target))
        if inclusive:
            end += 1
        if start == end:
            return
        self._apply_operator(operator, start, end, linewise=False)

    # ------------------------------------------------------------ operators

    def _apply_operator(
        self, operator: str, start: int, end: int, *, linewise: bool
    ) -> None:
        if not linewise:
            start, end = self._expand_range(start, end)
        text = self._text
        snippet = text[start:end]
        self._register = snippet
        self._register_linewise = linewise

        if operator == "y":
            self._move(start, select=False)
            self.enter_normal()
            return
        self.ta.delete(self._loc(start), self._loc(end))
        if operator == "c":
            self.enter_insert()
        else:
            self.enter_normal()
            self._clamp_to_line()

    def _operate_lines(self, start_row: int, end_row: int) -> None:
        operator = self._operator
        self._operator = None
        if operator is None:
            return
        doc = self._document
        end_row = min(end_row, doc.line_count - 1)
        start_idx = doc.get_index_from_location((start_row, 0))
        end_idx = doc.get_index_from_location((end_row, len(doc.get_line(end_row))))
        text = self._text
        self._register = text[start_idx:end_idx] + "\n"
        self._register_linewise = True

        if operator == "y":
            self._move(start_idx, select=False)
            self.enter_normal()
            return
        if operator == "c":
            self.ta.delete(self._loc(start_idx), self._loc(end_idx))
            self.enter_insert()
            return
        delete_end = min(end_idx + 1, len(text))  # take the trailing newline
        if end_idx == len(text) and start_idx > 0:
            start_idx -= 1  # last line: consume the preceding newline instead
        self.ta.delete(self._loc(start_idx), self._loc(delete_end))
        self.enter_normal()
        self._move(
            first_non_blank(self._text, min(start_idx, len(self._text))), select=False
        )

    # ------------------------------------------------------------------ keys

    def _handle_normal_key(self, char: str) -> None:
        if char in "hjklwbe0^$G":
            self._motion_target(char)
            return
        if char == "g":
            self._pending = "g"
            return
        if char in "fFtT":
            self._pending = char
            return
        if char in ";,":
            if self._last_find is None:
                return
            find_c, kind = self._last_find
            if char == ",":
                kind = {"f": "F", "F": "f", "t": "T", "T": "t"}[kind]
            self._apply_find(find_c, kind)
            if char == ",":
                self._last_find = (
                    find_c,
                    {"f": "F", "F": "f", "t": "T", "T": "t"}[kind],
                )
            return

        if char in "dcy":
            if self._operator == char:
                row = self.ta.cursor_location[0]
                self._operate_lines(row, row + self._take_count() - 1)
                return
            if self._operator is not None:
                self._clear_pending()
                return
            self._operator = char
            return
        if char in "ia" and self._operator is not None:
            self._pending = "iobj" if char == "i" else "aobj"
            return
        if self._operator is not None:
            # Unknown motion after an operator: abort.
            self._clear_pending()
            return

        text, idx = self._text, self._idx
        if char == "i":
            self.enter_insert()
        elif char == "a":
            _, end = line_bounds(text, idx)
            self._move(min(idx + 1, end), select=False)
            self.enter_insert()
        elif char == "I":
            self._move(first_non_blank(text, idx), select=False)
            self.enter_insert()
        elif char == "A":
            _, end = line_bounds(text, idx)
            self._move(end, select=False)
            self.enter_insert()
        elif char == "o":
            _, end = line_bounds(text, idx)
            self.ta.insert("\n", self._loc(end))
            self.enter_insert()
        elif char == "O":
            start, _ = line_bounds(text, idx)
            self.ta.insert("\n", self._loc(start))
            self._move(start, select=False)
            self.enter_insert()
        elif char == "v":
            self.mode = Mode.VISUAL
        elif char == "V":
            self.mode = Mode.VISUAL_LINE
            self._expand_visual_line()
        elif char == "x":
            count = self._take_count()
            _, end = line_bounds(text, idx)
            end_idx = min(idx + count, end)
            start_idx, end_idx = self._expand_range(idx, end_idx)
            if end_idx > start_idx:
                self._register = text[start_idx:end_idx]
                self._register_linewise = False
                self.ta.delete(self._loc(start_idx), self._loc(end_idx))
                self._clamp_to_line()
        elif char == "X":
            count = self._take_count()
            start, _ = line_bounds(text, idx)
            start_idx = max(idx - count, start)
            if start_idx < idx:
                start_idx, end_idx = self._expand_range(start_idx, idx)
                self.ta.delete(self._loc(start_idx), self._loc(end_idx))
        elif char == "D":
            _, end = line_bounds(text, idx)
            if end > idx:
                self._register = text[idx:end]
                self._register_linewise = False
                self.ta.delete(self._loc(idx), self._loc(end))
                self._clamp_to_line()
        elif char == "C":
            _, end = line_bounds(text, idx)
            self.ta.delete(self._loc(idx), self._loc(end))
            self.enter_insert()
        elif char == "s":
            count = self._take_count()
            _, end = line_bounds(text, idx)
            start_idx, end_idx = self._expand_range(idx, min(idx + count, end))
            self.ta.delete(self._loc(start_idx), self._loc(end_idx))
            self.enter_insert()
        elif char == "S":
            start, end = line_bounds(text, idx)
            self.ta.delete(self._loc(start), self._loc(end))
            self.enter_insert()
        elif char == "r":
            self._pending = "r"
        elif char == "~":
            _, end = line_bounds(text, idx)
            atom = self._atom_at(idx)
            if atom is not None:
                # Atoms have no case; step over instead of mangling them.
                self._move(min(atom[1], max(end - 1, 0)), select=False)
            elif idx < end:
                self.ta.replace(
                    text[idx].swapcase(), self._loc(idx), self._loc(idx + 1)
                )
                self._move(min(idx + 1, max(end - 1, 0)), select=False)
        elif char == "p":
            self._paste(after=True)
        elif char == "P":
            self._paste(after=False)
        elif char == "u":
            self.ta.undo()
            self._clear_pending()

    def _handle_visual_key(self, char: str) -> bool:
        """Visual-only keys. Returns True when handled."""
        if char in "dxcy":
            operator = "y" if char == "y" else ("c" if char == "c" else "d")
            start_loc, end_loc = sorted(
                (self.ta.selection.start, self.ta.selection.end)
            )
            doc = self._document
            start = doc.get_index_from_location(start_loc)
            end = doc.get_index_from_location(end_loc)
            if self.mode is Mode.VISUAL_LINE:
                self._operator = operator
                self._operate_lines(start_loc[0], end_loc[0])
                return True
            if self.mode is Mode.VISUAL:
                # Visual selections are inclusive of the cursor character.
                _, line_end = line_bounds(self._text, end)
                end = min(end + 1, line_end if line_end > end else end + 1)
            if end <= start:
                end = start + 1
            self._apply_operator(operator, start, end, linewise=False)
            return True
        if char == "o":
            sel = self.ta.selection
            self.ta.selection = sel.__class__(sel.end, sel.start)
            return True
        if char in "ia":
            self._pending = "iobj" if char == "i" else "aobj"
            return True
        if char == "v":
            if self.mode is Mode.VISUAL:
                self.enter_normal()
            else:
                self.mode = Mode.VISUAL
            return True
        if char == "V":
            if self.mode is Mode.VISUAL_LINE:
                self.enter_normal()
            else:
                self.mode = Mode.VISUAL_LINE
                self._expand_visual_line()
            return True
        return False

    # ------------------------------------------------------------------ misc

    def _expand_visual_line(self) -> None:
        sel = self.ta.selection
        doc = self._document
        start_row, end_row = sel.start[0], sel.end[0]
        if start_row <= end_row:
            self.ta.selection = sel.__class__(
                (start_row, 0), (end_row, len(doc.get_line(end_row)))
            )
        else:
            self.ta.selection = sel.__class__(
                (start_row, len(doc.get_line(start_row))), (end_row, 0)
            )

    def _paste(self, *, after: bool) -> None:
        if not self._register:
            return
        text, idx = self._text, self._idx
        if self._register_linewise:
            start, end = line_bounds(text, idx)
            if after:
                if end == len(text):
                    self.ta.insert("\n" + self._register.rstrip("\n"), self._loc(end))
                else:
                    self.ta.insert(self._register, self._loc(end + 1))
                self._move(min(end + 1, len(self._text)), select=False)
            else:
                self.ta.insert(self._register, self._loc(start))
                self._move(start, select=False)
            return
        _, line_end = line_bounds(text, idx)
        insert_at = min(idx + 1, line_end) if after else idx
        self.ta.insert(self._register, self._loc(insert_at))
        self._move(insert_at + len(self._register) - 1, select=False)

    def _clamp_to_line(self) -> None:
        """Keep the normal-mode cursor on the last character of the line."""
        idx = self._idx
        start, end = line_bounds(self._text, idx)
        if idx >= end and end > start:
            self._move(end - 1, select=False)

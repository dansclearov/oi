"""LaTeX math rendered as kitty-graphics images for the TUI.

A formula is rasterized with matplotlib's mathtext (Computer Modern, no TeX
install needed) into an RGBA image whose pixel size is an exact multiple of
the terminal cell, painted in the terminal's own foreground color on a
transparent background, then transmitted with the kitty graphics protocol
as a *virtual placement*. Virtual placements are shown through Unicode
placeholder characters (U+10EEEE plus row/column diacritics, image id in
the cell's foreground color), so an image is just a run of styled text
cells: inline math sits inside a paragraph's `Content` on the text
baseline, display math is a block of placeholder lines.

`probe_terminal()` must run before Textual starts (it needs the tty to
answer queries) and decides whether any of this is on; without it every
render returns `None` and the markdown layer shows the LaTeX source.

Layout beyond a single mathtext expression — `\\` rows, `&` alignment,
`align`/`gather`-style environments, and `pmatrix`/`bmatrix`/`cases`/`array`
grids with delimiters scaled to the grid — is composed here from separately
rasterized pieces, because mathtext has no environments at all.
"""

from __future__ import annotations

import base64
import io
import os
import re
import sys
import threading
from dataclasses import dataclass, replace
from itertools import count
from random import randint
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from textual.color import Color
from textual.style import Style

if TYPE_CHECKING:
    import numpy as np

# Font em relative to the cell height, and where the text baseline sits in a
# cell. Terminal fonts typically render at ~cell_height/1.25 em with the
# baseline around 80% down the cell; Computer Modern's x-height is smaller
# than a monospace font's, so the em is kept on the generous side.
EM_FACTOR = 0.84
BASELINE_FACTOR = 0.77
# TeX's math axis (the height fractions and operators center on) above baseline.
AXIS_FACTOR = 0.25
SUPERSAMPLE = 4
# Below this, inline math is unreadable; shift the baseline instead of shrinking.
MIN_INLINE_SCALE = 0.6
# Inline glyphs may overhang the cell by this many screen pixels before shrinking.
INLINE_OVERHANG = 0.75
MAX_BASELINE_SHIFT = 0.2  # of the cell height

ROW_GAP = 0.35  # em, between stacked rows
COL_GAP = 1.0  # em, between matrix columns
ALIGN_GAP = 0.15  # em, either side of an `&` alignment point
DELIM_GAP = 0.15  # em

PLACEHOLDER = chr(0x10EEEE)


ALIGN_ENVS = {
    "align",
    "align*",
    "aligned",
    "alignat",
    "alignat*",
    "alignedat",
    "gather",
    "gather*",
    "gathered",
    "equation",
    "equation*",
    "split",
    "eqnarray",
    "eqnarray*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
}
# env → (left delimiter, right delimiter, default column alignment)
GRID_ENVS: dict[str, tuple[str, str, str]] = {
    "matrix": ("", "", "c"),
    "pmatrix": ("(", ")", "c"),
    "bmatrix": ("[", "]", "c"),
    "Bmatrix": (r"\{", r"\}", "c"),
    "vmatrix": ("|", "|", "c"),
    "Vmatrix": (r"\|", r"\|", "c"),
    "smallmatrix": ("", "", "c"),
    "cases": (r"\{", "", "l"),
    "rcases": ("", r"\}", "l"),
    "array": ("", "", "c"),
}

_DELIMS = {
    "(": "(",
    ")": ")",
    "[": "[",
    "]": "]",
    r"\{": r"\{",
    r"\}": r"\}",
    r"\lbrace": r"\{",
    r"\rbrace": r"\}",
    "|": "|",
    r"\|": r"\|",
    r"\vert": "|",
    r"\Vert": r"\|",
    r"\lvert": "|",
    r"\rvert": "|",
    r"\lVert": r"\|",
    r"\rVert": r"\|",
    r"\langle": r"\langle",
    r"\rangle": r"\rangle",
    r"\lfloor": r"\lfloor",
    r"\rfloor": r"\rfloor",
    r"\lceil": r"\lceil",
    r"\rceil": r"\rceil",
    ".": "",
}
_DELIM_RE = "|".join(re.escape(d) for d in sorted(_DELIMS, key=len, reverse=True))
_LEFT_TAIL_RE = re.compile(rf"\\left\s*({_DELIM_RE})\s*$")
_RIGHT_HEAD_RE = re.compile(rf"^\s*\\right\s*({_DELIM_RE})")
_ROW_LENGTH_RE = re.compile(r"^\s*\[\s*-?[\d.]+\s*(pt|em|ex|mm|cm|in|px)\s*\]")

_END = r"(?![a-zA-Z])"
# mathtext lacks these; each rewrite is the nearest thing it draws.
_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), r)
    for p, r in [
        (rf"\\le{_END}", r"\\leq"),
        (rf"\\ge{_END}", r"\\geq"),
        (rf"\\ne{_END}", r"\\neq"),
        (rf"\\[td]frac{_END}", r"\\frac"),
        (rf"\\iff{_END}", r"\\Leftrightarrow"),
        (rf"\\implies{_END}", r"\\Rightarrow"),
        (rf"\\impliedby{_END}", r"\\Leftarrow"),
        (rf"\\(?:display|text|script|scriptscript)style{_END}", ""),
        (rf"\\boxed{_END}", ""),
        (rf"\\bmod{_END}", r"\\;\\mathrm{mod}\\;"),
        (r"\\pmod\{([^{}]*)\}", r"\\;(\\mathrm{mod}\\ \1)"),
        (rf"\\stackrel{_END}", r"\\overset"),
        (r"\\xrightarrow\{([^{}]*)\}", r"\\overset{\1}{\\longrightarrow}"),
        (r"\\xleftarrow\{([^{}]*)\}", r"\\overset{\1}{\\longleftarrow}"),
        (rf"\\[lr]Vert{_END}", r"\\|"),
        (rf"\\[lr]vert{_END}", "|"),
        (rf"\\lbrace{_END}", r"\\{"),
        (rf"\\rbrace{_END}", r"\\}"),
        (rf"\\argmax{_END}", r"\\operatorname{arg\\,max}"),
        (rf"\\argmin{_END}", r"\\operatorname{arg\\,min}"),
        (r"\\color\{[^{}]*\}", ""),
        (r"\\textcolor\{[^{}]*\}", ""),
        (r"\\[hv]space\*?\{[^{}]*\}", r"\\quad"),
        (rf"\\[Bb]ig{{1,2}}[lrm]?{_END}", ""),
        (rf"\\underbrace{_END}", r"\\underline"),
        (rf"\\overbrace{_END}", r"\\overline"),
        (rf"\\textbf{_END}", r"\\mathbf"),
        (rf"\\textit{_END}", r"\\mathit"),
        (rf"\\textrm{_END}", r"\\mathrm"),
        (rf"\\texttt{_END}", r"\\mathtt"),
        (rf"\\textsf{_END}", r"\\mathsf"),
        (rf"\\mbox{_END}", r"\\text"),
        (rf"\\mathscr{_END}", r"\\mathcal"),
        (r"\\operatorname\*", r"\\operatorname"),
        (rf"\\(?:nonumber|notag|hline|qquad\*){_END}", ""),
        (r"\\(?:label|tag)\*?\{[^{}]*\}", ""),
        (rf"\\ensuremath{_END}", ""),
        (rf"\\(?:newline|cr){_END}", r"\\\\"),
        (rf"\\vphantom{_END}", r"\\phantom"),
    ]
]


class MathError(Exception):
    """The expression can't be rendered; the caller falls back to the source."""


@dataclass(frozen=True)
class TerminalMetrics:
    cell_width: int
    cell_height: int
    foreground: tuple[int, int, int]
    columns: int = 80


@dataclass(frozen=True)
class MathImage:
    """A formula transmitted to the terminal, addressed by placeholder cells."""

    image_id: int
    cols: int
    rows: int

    def placeholder_row(self, row: int) -> str:
        id_char = chr(DIACRITICS[(self.image_id >> 24) & 255])
        return "".join(
            f"{PLACEHOLDER}{chr(DIACRITICS[row])}{chr(DIACRITICS[col])}{id_char}"
            for col in range(self.cols)
        )

    @property
    def style(self) -> Style:
        i = self.image_id
        return Style(foreground=Color((i >> 16) & 255, (i >> 8) & 255, i & 255))


Sender = Callable[[dict[str, int | str], Optional[str]], None]

_metrics: TerminalMetrics | None = None
_sender: Sender | None = None
_cache: dict[tuple[str, bool, int | None], MathImage | None] = {}
_ids = count(randint(1, 2**32 - 1))
_backend_lock = threading.Lock()
_parser = None


def configure(metrics: TerminalMetrics | None, sender: Sender | None = None) -> None:
    """Turn rendering on (with the terminal's geometry) or off (`None`)."""
    global _metrics, _sender
    _metrics = metrics
    _sender = sender if sender is not None else _send_tgp
    _cache.clear()


def enabled() -> bool:
    return _metrics is not None


def metrics() -> TerminalMetrics | None:
    return _metrics


def deps_available() -> bool:
    from importlib.util import find_spec

    return find_spec("matplotlib") is not None


def warm() -> None:
    """Import matplotlib on a background thread (it costs a few hundred ms)."""
    threading.Thread(target=_backend, name="mathtext-warmup", daemon=True).start()


def render(tex: str, *, display: bool, max_cols: int | None = None) -> MathImage | None:
    """Rasterize + transmit `tex`, or return `None` when it can't be shown.

    `max_cols` caps the width of display math (it's scaled down to fit);
    the natural-size render is cached separately so a cap that doesn't bind
    never re-transmits.
    """
    if _metrics is None:
        return None
    tex = tex.strip()
    if not tex:
        return None
    key = (tex, display, None)
    if key not in _cache:
        _cache[key] = _render_uncached(tex, display, None)
    natural = _cache[key]
    if natural is None or max_cols is None or natural.cols <= max_cols:
        return natural
    key = (tex, display, max_cols)
    if key not in _cache:
        _cache[key] = _render_uncached(tex, display, max_cols)
    return _cache[key]


# --- rasterization -----------------------------------------------------------


@dataclass(frozen=True)
class Box:
    """An alpha mask with its baseline: `ascent` rows above, `depth` below."""

    alpha: "np.ndarray"
    ascent: int
    depth: int

    @property
    def height(self) -> int:
        return int(self.alpha.shape[0])

    @property
    def width(self) -> int:
        return int(self.alpha.shape[1])


_BIG_OPERATOR_RE = re.compile(
    r"\\(sum|prod|coprod|bigcup|bigcap|bigoplus|bigotimes|bigodot|bigsqcup|biguplus"
    r"|bigvee|bigwedge|lim|limsup|liminf|max|min|sup|inf|det|gcd)(?![a-zA-Z])"
)


_SMALL_OPERATORS = {
    "sum": r"\Sigma",
    "prod": r"\Pi",
    "coprod": r"\amalg",
    "bigcup": r"\cup",
    "bigcap": r"\cap",
    "bigvee": r"\vee",
    "bigwedge": r"\wedge",
    "bigoplus": r"\oplus",
    "bigotimes": r"\otimes",
}
_SMALL_OPERATOR_RE = re.compile(r"\\(" + "|".join(_SMALL_OPERATORS) + r")(?![a-zA-Z])")


_GROUP = r"\{((?:[^{}]|\{[^{}]*\})*)\}"
_INLINE_FRAC_RE = re.compile(r"\\frac\s*" + _GROUP + r"\s*" + _GROUP)


def _case_fractions(tex: str) -> str:
    """`\\frac{a}{b}` → a case fraction (script-size numerator/denominator
    around a slash): a stacked fraction is taller than a terminal row."""
    while True:
        tex, n = _INLINE_FRAC_RE.subn(r"{}^{\1}\\!/\\!{}_{\2}", tex)
        if not n:
            return tex


def _textstyle(tex: str) -> str:
    """Inline TeX uses text-size operators with limits beside them; mathtext
    only has the display glyphs (1.5 cells tall) and always stacks limits —
    unless the operator is a group of its own."""
    tex = _SMALL_OPERATOR_RE.sub(
        lambda m: "{" + _SMALL_OPERATORS[m.group(1)] + "}", tex
    )
    return _BIG_OPERATOR_RE.sub(r"{\\\1}", _case_fractions(tex))


def _displaystyle(tex: str) -> str:
    """Promote top-level fractions to display size (`\\dfrac`), as TeX does."""
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(tex):
        if (
            tex.startswith("\\frac", i)
            and depth == 0
            and not tex[i + 5 : i + 6].isalpha()
        ):
            out.append("\\dfrac")
            i += 5
            continue
        ch = tex[i]
        if depth > 0 and (m := _BIG_OPERATOR_RE.match(tex, i)):
            # Fraction parts and scripts are text style: limits go beside.
            out.append("{" + m.group(0) + "}")
            i = m.end()
            continue
        if ch == "\\" and i + 1 < len(tex):
            out.append(tex[i : i + 2])
            i += 2
            continue
        depth += (ch == "{") - (ch == "}")
        out.append(ch)
        i += 1
    return "".join(out)


class _Rasterizer:
    def __init__(self, em: float, display: bool):
        self.em = em  # font size in (supersampled) pixels
        self.display = display
        self.parser, self.font_properties = _backend()

    def glyph(self, tex: str, em: float | None = None) -> Box:
        import numpy as np

        tex = tex.strip()
        if not tex:
            return Box(np.zeros((1, 1), dtype=np.float32), 1, 0)
        tex = re.sub(r"\s+", " ", tex)  # mathtext rejects newlines
        tex = _displaystyle(tex) if self.display else _textstyle(tex)
        prop = self.font_properties.copy()
        prop.set_size(em or self.em)
        try:
            parsed = self.parser.parse(f"${tex}$", dpi=72, prop=prop)
        except Exception as exc:  # mathtext raises pyparsing exceptions
            raise MathError(str(exc)) from exc
        alpha = np.asarray(parsed.image, dtype=np.float32)
        depth = int(round(parsed.depth))
        return Box(alpha, alpha.shape[0] - depth, depth)

    # -- composition --

    def hbox(self, boxes: Sequence[Box], gap: float = 0.0) -> Box:
        import numpy as np

        boxes = [b for b in boxes if b.width > 1 or b.height > 1] or list(boxes)
        gap_px = int(round(gap * self.em))
        ascent = max(b.ascent for b in boxes)
        depth = max(b.depth for b in boxes)
        width = sum(b.width for b in boxes) + gap_px * (len(boxes) - 1)
        canvas = np.zeros((ascent + depth, width), dtype=np.float32)
        x = 0
        for box in boxes:
            y = ascent - box.ascent
            canvas[y : y + box.height, x : x + box.width] = np.maximum(
                canvas[y : y + box.height, x : x + box.width], box.alpha
            )
            x += box.width + gap_px
        return Box(canvas, ascent, depth)

    def vstack(
        self,
        boxes: Sequence[Box],
        *,
        align: str = "c",
        anchors: Sequence[int] | None = None,
        gap: float = ROW_GAP,
    ) -> Box:
        """Stack rows; the result is centered on the math axis."""
        import numpy as np

        gap_px = int(round(gap * self.em))
        if anchors is not None:
            anchor = max(anchors)
            offsets = [anchor - a for a in anchors]
            width = max(o + b.width for o, b in zip(offsets, boxes))
        else:
            width = max(b.width for b in boxes)
            offsets = [
                0 if align == "l" else (width - b.width) // (1 if align == "r" else 2)
                for b in boxes
            ]
        height = sum(b.height for b in boxes) + gap_px * (len(boxes) - 1)
        canvas = np.zeros((height, width), dtype=np.float32)
        y = 0
        for box, x in zip(boxes, offsets):
            canvas[y : y + box.height, x : x + box.width] = box.alpha
            y += box.height + gap_px
        return self.centered(Box(canvas, height, 0))

    def centered(self, box: Box) -> Box:
        """Re-baseline a box so its vertical middle sits on the math axis."""
        axis = int(round(AXIS_FACTOR * self.em))
        ascent = box.height // 2 + axis
        return replace(box, ascent=ascent, depth=box.height - ascent)

    def delimiter(self, symbol: str, height: int) -> Box:
        """A delimiter glyph scaled to span `height` pixels."""
        probe = self.glyph(symbol)
        return self.glyph(symbol, em=self.em * height / probe.height)

    # -- structure --

    def layout(self, tex: str) -> Box:
        rows = [r for r in _split_top(tex, r"\\") if r.strip()]
        if len(rows) > 1:
            return self.rows(rows)
        return self.row(tex)

    def rows(self, rows: list[str]) -> Box:
        parts = [_split_top(_ROW_LENGTH_RE.sub("", r), "&") for r in rows]
        if all(len(p) == 1 for p in parts):
            return self.vstack([self.line(p[0]) for p in parts])
        boxes: list[Box] = []
        anchors: list[int] = []
        for p in parts:
            left = self.line(p[0])
            if len(p) == 1:
                boxes.append(left)
                anchors.append(left.width // 2)
                continue
            right = self.line("".join(p[1:]))
            boxes.append(self.hbox([left, right], gap=ALIGN_GAP))
            anchors.append(left.width)
        return self.vstack(boxes, anchors=anchors)

    def row(self, tex: str) -> Box:
        parts = _split_top(tex, "&")
        if len(parts) == 1:
            return self.line(tex)
        return self.hbox(
            [self.line(parts[0]), self.line("".join(parts[1:]))], gap=ALIGN_GAP
        )

    def line(self, tex: str) -> Box:
        segments = _split_envs(tex)
        if len(segments) == 1 and isinstance(segments[0], str):
            return self.glyph(segments[0])
        boxes: list[Box] = []
        delims: tuple[str, str] = ("", "")
        for i, seg in enumerate(segments):
            if isinstance(seg, _Env):
                boxes.append(self.environment(seg, *delims))
                delims = ("", "")
                continue
            # `\left( <env> \right)`: the pair spans two text segments, which
            # mathtext can't parse apart, so it becomes the env's delimiters.
            after = segments[i + 2] if i + 2 < len(segments) else None
            lm = _LEFT_TAIL_RE.search(seg) if isinstance(after, str) else None
            rm = _RIGHT_HEAD_RE.match(after) if lm and isinstance(after, str) else None
            if lm and rm:
                assert isinstance(after, str)
                delims = (_DELIMS[lm.group(1)], _DELIMS[rm.group(1)])
                seg = seg[: lm.start()]
                segments[i + 2] = after[rm.end() :]
            if seg.strip():
                boxes.append(self.glyph(seg))
        return self.hbox(boxes, gap=DELIM_GAP)

    def environment(self, env: "_Env", left: str, right: str) -> Box:
        if env.name in ALIGN_ENVS:
            body = self.layout(env.body)
        elif env.name in GRID_ENVS:
            env_left, env_right, align = GRID_ENVS[env.name]
            left, right = left or env_left, right or env_right
            body = self.grid(env.body, env.colspec or align)
        else:
            raise MathError(f"unsupported environment {env.name}")
        parts = [body]
        if left:
            parts.insert(0, self.fit_delimiter(left, body))
        if right:
            parts.append(self.fit_delimiter(right, body))
        return self.hbox(parts, gap=DELIM_GAP)

    def fit_delimiter(self, symbol: str, body: Box) -> Box:
        delim = self.delimiter(symbol, body.height)
        return replace(
            delim,
            ascent=body.ascent - (body.height - delim.height) // 2,
            depth=body.depth - (body.height - delim.height + 1) // 2,
        )

    def grid(self, body: str, colspec: str) -> Box:
        import numpy as np

        aligns = [c for c in colspec if c in "lcr"] or ["c"]
        rows = [
            _split_top(_ROW_LENGTH_RE.sub("", r), "&") for r in _split_top(body, r"\\")
        ]
        rows = [r for r in rows if any(c.strip() for c in r)]
        cells = [[self.line(c) for c in r] for r in rows]
        ncols = max(len(r) for r in cells)
        widths = [
            max((r[c].width for r in cells if c < len(r)), default=1)
            for c in range(ncols)
        ]
        row_boxes: list[Box] = []
        col_gap = int(round(COL_GAP * self.em))
        for r in cells:
            ascent = max(b.ascent for b in r)
            depth = max(b.depth for b in r)
            width = sum(widths) + col_gap * (ncols - 1)
            canvas = np.zeros((ascent + depth, width), dtype=np.float32)
            x = 0
            for c, box in enumerate(r):
                align = aligns[min(c, len(aligns) - 1)]
                slack = widths[c] - box.width
                dx = 0 if align == "l" else slack if align == "r" else slack // 2
                y = ascent - box.ascent
                canvas[y : y + box.height, x + dx : x + dx + box.width] = box.alpha
                x += widths[c] + col_gap
            row_boxes.append(Box(canvas, ascent, depth))
        return self.vstack(row_boxes, align="l")


@dataclass
class _Env:
    name: str
    colspec: str
    body: str


_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")


def _split_envs(tex: str) -> list[str | _Env]:
    """Split on top-level `\\begin{..}...\\end{..}` groups, keeping the text between."""
    out: list[str | _Env] = []
    pos = 0
    while True:
        m = _BEGIN_RE.search(tex, pos)
        if m is None:
            break
        name = m.group(1)
        depth = 1
        scan = m.end()
        colspec = ""
        if name == "array":
            cm = re.match(r"\s*\{([^{}]*)\}", tex[scan:])
            if cm:
                colspec = cm.group(1)
                scan += cm.end()
        body_start = scan
        while depth:
            b, e = _BEGIN_RE.search(tex, scan), _END_RE.search(tex, scan)
            if e is None:
                raise MathError(f"unterminated \\begin{{{name}}}")
            if b is not None and b.start() < e.start():
                depth += 1
                scan = b.end()
            else:
                depth -= 1
                scan = e.end()
                end = e
        out.append(tex[pos : m.start()])
        out.append(_Env(name, colspec, tex[body_start : end.start()]))
        pos = scan
    out.append(tex[pos:])
    return [s for s in out if not (isinstance(s, str) and not s.strip())] or [""]


def _split_top(tex: str, sep: str) -> list[str]:
    """Split on `sep` outside braces and environments (`sep` is `\\\\` or `&`)."""
    parts: list[str] = []
    depth = 0
    env = 0
    start = 0
    i = 0
    n = len(tex)
    while i < n:
        ch = tex[i]
        if ch == "\\":
            if tex.startswith("\\begin{", i):
                env += 1
            elif tex.startswith("\\end{", i):
                env -= 1
            if sep == r"\\" and tex.startswith("\\\\", i) and depth == 0 and env == 0:
                parts.append(tex[start:i])
                i += 2
                start = i
                continue
            i += 2  # skip the escaped character
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == sep and depth == 0 and env == 0:
            parts.append(tex[start:i])
            start = i + 1
        i += 1
    parts.append(tex[start:])
    return parts


_DOUBLED_COMMAND_RE = re.compile(r"\\\\(?=[a-zA-Z])")


def preprocess(tex: str) -> str:
    # `R(\\theta)`: a doubled backslash before a letter is a model's escaping
    # slip, never a row break followed by prose.
    tex = _DOUBLED_COMMAND_RE.sub(r"\\", tex)
    for pattern, repl in _ALIASES:
        tex = pattern.sub(repl, tex)
    return tex


def _backend():
    """Import matplotlib once (behind a lock, see `warm`) and build the parser."""
    global _parser
    with _backend_lock:
        if _parser is None:
            import matplotlib

            matplotlib.use("agg")
            matplotlib.rcParams["mathtext.fontset"] = "cm"
            from matplotlib.font_manager import FontProperties
            from matplotlib.mathtext import MathTextParser

            _parser = (MathTextParser("agg"), FontProperties())
        return _parser


def _render_uncached(tex: str, display: bool, max_cols: int | None) -> MathImage | None:
    assert _metrics is not None and _sender is not None
    try:
        rgba, cols, rows = rasterize(tex, _metrics, display=display, max_cols=max_cols)
    except MathError:
        return None
    image_id = next(_ids)
    _transmit(image_id, rgba, cols, rows)
    return MathImage(image_id, cols, rows)


def rasterize(
    tex: str, metrics: TerminalMetrics, *, display: bool, max_cols: int | None = None
):
    """Return `(RGBA PIL image, cols, rows)` for `tex` at cell-exact pixel size."""
    import numpy as np
    from PIL import Image

    ss = SUPERSAMPLE
    cell_w, cell_h = metrics.cell_width * ss, metrics.cell_height * ss
    em = EM_FACTOR * cell_h
    box = _Rasterizer(em, display).layout(preprocess(tex))
    scale = 1.0
    if display:
        pad = int(round(0.15 * em))
        rows = max(1, -(-(box.height + 2 * pad) // cell_h))
        cols = -(-(box.width + 2 * pad) // cell_w)
        if max_cols is not None and cols > max_cols:
            scale = (max_cols * cell_w - 2 * pad) / box.width
            box = _scaled(box, scale)
            rows = max(1, -(-(box.height + 2 * pad) // cell_h))
            cols = max_cols
        canvas = np.zeros((rows * cell_h, cols * cell_w), dtype=np.float32)
        top = (canvas.shape[0] - box.height) // 2
        left = (canvas.shape[1] - box.width) // 2
    else:
        base_y = int(round(BASELINE_FACTOR * cell_h))
        margin = ss  # one screen pixel
        overhang = int(INLINE_OVERHANG * ss)
        room_above, room_below = base_y + overhang, cell_h - base_y + overhang
        shift = 0
        if box.ascent > room_above:  # borrow from below before shrinking
            shift = min(
                box.ascent - room_above,
                room_below - box.depth,
                int(MAX_BASELINE_SHIFT * cell_h),
            )
            shift = max(shift, 0)
        scale = min(
            1.0,
            room_above / max(box.ascent - shift, 1),
            (room_below - shift) / max(box.depth, 1),
        )
        scale = max(scale, MIN_INLINE_SCALE)
        if scale < 1.0:
            box = _scaled(box, scale)
        rows = 1
        cols = max(1, -(-(box.width + 2 * margin) // cell_w))
        canvas = np.zeros((cell_h, cols * cell_w), dtype=np.float32)
        top = base_y + shift - box.ascent
        top = max(0, min(top, cell_h - box.height))
        left = margin
    _paste(canvas, box.alpha, top, left)
    rgba = np.zeros((*canvas.shape, 4), dtype=np.uint8)
    rgba[..., :3] = metrics.foreground
    rgba[..., 3] = np.clip(canvas, 0, 255).astype(np.uint8)
    image = Image.fromarray(rgba, "RGBA").resize(
        (cols * metrics.cell_width, rows * metrics.cell_height),
        Image.Resampling.LANCZOS,
    )
    return image, cols, rows


def _paste(canvas, alpha, top: int, left: int) -> None:
    """Copy `alpha` onto `canvas` at (top, left), clipping whatever overhangs."""
    h, w = alpha.shape
    y0, x0 = max(0, -top), max(0, -left)
    y1 = min(h, canvas.shape[0] - top)
    x1 = min(w, canvas.shape[1] - left)
    if y1 > y0 and x1 > x0:
        canvas[top + y0 : top + y1, left + x0 : left + x1] = alpha[y0:y1, x0:x1]


def _scaled(box: Box, scale: float) -> Box:
    import numpy as np
    from PIL import Image

    img = Image.fromarray(np.clip(box.alpha, 0, 255).astype(np.uint8), "L")
    size = (max(1, int(box.width * scale)), max(1, int(box.height * scale)))
    alpha = np.asarray(img.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)
    ascent = int(round(box.ascent * scale))
    return Box(alpha, ascent, alpha.shape[0] - ascent)


# --- terminal ----------------------------------------------------------------


def _transmit(image_id: int, image, cols: int, rows: int) -> None:
    assert _sender is not None
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    while data:
        chunk, data = data[:4096], data[4096:]
        _sender({"i": image_id, "m": 1 if data else 0, "f": 100, "q": 2}, chunk)
    _sender({"a": "p", "i": image_id, "c": cols, "r": rows, "U": 1, "q": 2}, None)


def _tgp_sequence(control: dict[str, int | str], payload: str | None) -> str:
    body = ",".join(f"{k}={v}" for k, v in control.items())
    seq = f"\x1b_G{body}" + (f";{payload}" if payload else "") + "\x1b\\"
    if "TMUX" in os.environ:  # needs tmux's allow-passthrough
        seq = "\x1bPtmux;" + seq.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    return seq


def _send_tgp(control: dict[str, int | str], payload: str | None) -> None:
    assert sys.__stdout__ is not None
    sys.__stdout__.write(_tgp_sequence(control, payload))
    sys.__stdout__.flush()


_TERMINAL_HINTS = (
    "KITTY_WINDOW_ID",
    "GHOSTTY_RESOURCES_DIR",
    "WEZTERM_PANE",
    "KONSOLE_VERSION",
)


def terminal_may_support_graphics() -> bool:
    """Cheap env check so non-graphics terminals never pay the query timeouts."""
    term = os.environ.get("TERM", "") + os.environ.get("TERM_PROGRAM", "")
    return any(
        h in term.lower() for h in ("kitty", "ghostty", "wezterm", "konsole")
    ) or any(v in os.environ for v in _TERMINAL_HINTS)


def probe_terminal() -> TerminalMetrics | None:
    """Query the tty for graphics support and geometry; call before Textual starts."""
    if not sys.__stdout__ or not sys.__stdout__.isatty() or not sys.__stdin__:
        return None
    if not terminal_may_support_graphics() or not deps_available():
        return None
    if not _query_graphics_support():
        return None
    cell_width, cell_height, columns = _cell_geometry()
    if cell_width < 2 or cell_height < 4:
        return None
    return TerminalMetrics(cell_width, cell_height, _query_foreground(), columns)


def _query(sequence: str, done: Callable[[str], bool], timeout: float = 0.2) -> str:
    """Write `sequence` and collect the reply until `done` accepts it or time runs out."""
    import termios
    import tty
    from select import select

    assert sys.__stdin__ and sys.__stdout__
    fd = sys.__stdin__.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd, termios.TCSANOW)
    response = ""
    try:
        sys.__stdout__.write(sequence)
        sys.__stdout__.flush()
        while not done(response):
            readable, _, _ = select([fd], [], [], timeout)
            if not readable:
                break
            response += os.read(fd, 64).decode("utf-8", "replace")
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)
    return response


def _query_graphics_support() -> bool:
    reply = _query(
        _tgp_sequence({"i": 31, "s": 1, "v": 1, "a": "q", "t": "d", "f": 24}, "AAAA"),
        lambda r: r.endswith("\x1b\\"),
    )
    return "\x1b_Gi=31;OK" in reply


def _cell_geometry() -> tuple[int, int, int]:
    """Cell pixel size and column count: ioctl first, `CSI 16 t` as a fallback."""
    import termios
    from array import array
    from fcntl import ioctl

    assert sys.__stdout__
    buf = array("H", [0, 0, 0, 0])
    ioctl(sys.__stdout__.fileno(), termios.TIOCGWINSZ, buf)
    rows, columns, xpixel, ypixel = buf
    if rows and columns and xpixel and ypixel:
        return xpixel // columns, ypixel // rows, columns
    reply = _query("\x1b[16t", lambda r: r.endswith("t"))
    m = re.search(r"\x1b\[6;(\d+);(\d+)t", reply)
    if m is None:
        return 0, 0, columns or 80
    return int(m.group(2)), int(m.group(1)), columns or 80


def _query_foreground() -> tuple[int, int, int]:
    """OSC 10 query for the default foreground; light grey when unanswered."""
    reply = _query(
        "\x1b]10;?\x1b\\", lambda r: r.endswith("\x1b\\") or r.endswith("\x07")
    )
    return parse_osc_color(reply) or (208, 208, 208)


def parse_osc_color(reply: str) -> tuple[int, int, int] | None:
    """`rgb:RRRR/GGGG/BBBB` (each channel 1–4 hex digits, scaled to its width)."""
    m = re.search(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", reply)
    if m is None:
        return None
    r, g, b = (int(c, 16) * 255 // (16 ** len(c) - 1) for c in m.groups())
    return (r, g, b)


# The kitty graphics protocol's placeholder table: row/column numbers are
# encoded as these combining characters, in this order.
# fmt: off
DIACRITICS = [
     0x00305, 0x0030d, 0x0030e, 0x00310, 0x00312, 0x0033d, 0x0033e, 0x0033f, 0x00346, 0x0034a, 0x0034b, 0x0034c,
     0x00350, 0x00351, 0x00352, 0x00357, 0x0035b, 0x00363, 0x00364, 0x00365, 0x00366, 0x00367, 0x00368, 0x00369,
     0x0036a, 0x0036b, 0x0036c, 0x0036d, 0x0036e, 0x0036f, 0x00483, 0x00484, 0x00485, 0x00486, 0x00487, 0x00592,
     0x00593, 0x00594, 0x00595, 0x00597, 0x00598, 0x00599, 0x0059c, 0x0059d, 0x0059e, 0x0059f, 0x005a0, 0x005a1,
     0x005a8, 0x005a9, 0x005ab, 0x005ac, 0x005af, 0x005c4, 0x00610, 0x00611, 0x00612, 0x00613, 0x00614, 0x00615,
     0x00616, 0x00617, 0x00657, 0x00658, 0x00659, 0x0065a, 0x0065b, 0x0065d, 0x0065e, 0x006d6, 0x006d7, 0x006d8,
     0x006d9, 0x006da, 0x006db, 0x006dc, 0x006df, 0x006e0, 0x006e1, 0x006e2, 0x006e4, 0x006e7, 0x006e8, 0x006eb,
     0x006ec, 0x00730, 0x00732, 0x00733, 0x00735, 0x00736, 0x0073a, 0x0073d, 0x0073f, 0x00740, 0x00741, 0x00743,
     0x00745, 0x00747, 0x00749, 0x0074a, 0x007eb, 0x007ec, 0x007ed, 0x007ee, 0x007ef, 0x007f0, 0x007f1, 0x007f3,
     0x00816, 0x00817, 0x00818, 0x00819, 0x0081b, 0x0081c, 0x0081d, 0x0081e, 0x0081f, 0x00820, 0x00821, 0x00822,
     0x00823, 0x00825, 0x00826, 0x00827, 0x00829, 0x0082a, 0x0082b, 0x0082c, 0x0082d, 0x00951, 0x00953, 0x00954,
     0x00f82, 0x00f83, 0x00f86, 0x00f87, 0x0135d, 0x0135e, 0x0135f, 0x017dd, 0x0193a, 0x01a17, 0x01a75, 0x01a76,
     0x01a77, 0x01a78, 0x01a79, 0x01a7a, 0x01a7b, 0x01a7c, 0x01b6b, 0x01b6d, 0x01b6e, 0x01b6f, 0x01b70, 0x01b71,
     0x01b72, 0x01b73, 0x01cd0, 0x01cd1, 0x01cd2, 0x01cda, 0x01cdb, 0x01ce0, 0x01dc0, 0x01dc1, 0x01dc3, 0x01dc4,
     0x01dc5, 0x01dc6, 0x01dc7, 0x01dc8, 0x01dc9, 0x01dcb, 0x01dcc, 0x01dd1, 0x01dd2, 0x01dd3, 0x01dd4, 0x01dd5,
     0x01dd6, 0x01dd7, 0x01dd8, 0x01dd9, 0x01dda, 0x01ddb, 0x01ddc, 0x01ddd, 0x01dde, 0x01ddf, 0x01de0, 0x01de1,
     0x01de2, 0x01de3, 0x01de4, 0x01de5, 0x01de6, 0x01dfe, 0x020d0, 0x020d1, 0x020d4, 0x020d5, 0x020d6, 0x020d7,
     0x020db, 0x020dc, 0x020e1, 0x020e7, 0x020e9, 0x020f0, 0x02cef, 0x02cf0, 0x02cf1, 0x02de0, 0x02de1, 0x02de2,
     0x02de3, 0x02de4, 0x02de5, 0x02de6, 0x02de7, 0x02de8, 0x02de9, 0x02dea, 0x02deb, 0x02dec, 0x02ded, 0x02dee,
     0x02def, 0x02df0, 0x02df1, 0x02df2, 0x02df3, 0x02df4, 0x02df5, 0x02df6, 0x02df7, 0x02df8, 0x02df9, 0x02dfa,
     0x02dfb, 0x02dfc, 0x02dfd, 0x02dfe, 0x02dff, 0x0a66f, 0x0a67c, 0x0a67d, 0x0a6f0, 0x0a6f1, 0x0a8e0, 0x0a8e1,
     0x0a8e2, 0x0a8e3, 0x0a8e4, 0x0a8e5, 0x0a8e6, 0x0a8e7, 0x0a8e8, 0x0a8e9, 0x0a8ea, 0x0a8eb, 0x0a8ec, 0x0a8ed,
     0x0a8ee, 0x0a8ef, 0x0a8f0, 0x0a8f1, 0x0aab0, 0x0aab2, 0x0aab3, 0x0aab7, 0x0aab8, 0x0aabe, 0x0aabf, 0x0aac1,
     0x0fe20, 0x0fe21, 0x0fe22, 0x0fe23, 0x0fe24, 0x0fe25, 0x0fe26, 0x10a0f, 0x10a38, 0x1d185, 0x1d186, 0x1d187,
     0x1d188, 0x1d189, 0x1d1aa, 0x1d1ab, 0x1d1ac, 0x1d1ad, 0x1d242, 0x1d243, 0x1d244
]
# fmt: on

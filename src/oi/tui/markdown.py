"""Textual `Markdown` with LaTeX math rendered through `tui.latex`.

`$…$` / `\\(…\\)` inline math becomes a run of kitty placeholder cells inside
the paragraph's `Content`, sitting on the text baseline; `$$…$$` / `\\[…\\]`
and `\\begin{align}…` blocks become a `MarkdownMath` block of placeholder
lines. When math rendering is off (no graphics terminal, no matplotlib) or
an expression can't be rendered, the LaTeX source is shown instead.
"""

from __future__ import annotations

import copy
import re
from typing import Callable, Optional

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.token import Token
from mdit_py_plugins.amsmath import amsmath_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.texmath import texmath_plugin
from textual.content import Content, Span
from textual.events import Resize
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownBlock

from oi.tui import latex

INLINE_MATH_TOKENS = {"math_inline", "math_inline_double"}
BLOCK_MATH_TOKENS = {"math_block", "math_block_label", "amsmath"}


# Block rules that may end a paragraph — what markdown-it gives fences.
_PARAGRAPH_INTERRUPTERS = ["paragraph", "reference", "blockquote", "list"]


LATEX_FENCES = {"latex", "tex", "math"}


class MathMarkdownIt(MarkdownIt):
    """markdown-it that also reads closed ```latex fences as display math."""

    def parse(self, src: str, env=None) -> list[Token]:
        tokens = super().parse(src, env)
        if not latex.enabled():
            return tokens
        lines = src.splitlines()
        for token in tokens:
            if token.type != "fence" or token.info.strip().lower() not in LATEX_FENCES:
                continue
            assert token.map is not None
            closing = (
                lines[token.map[1] - 1].strip() if token.map[1] <= len(lines) else ""
            )
            if not closing.startswith(token.markup):
                continue  # still streaming
            if latex.render(token.content, display=True) is None:
                continue
            token.type = "math_block"
        return tokens


def make_parser() -> MarkdownIt:
    md = (
        MathMarkdownIt("gfm-like")
        # `allow_digits=False` keeps "$5 and $6" from becoming math.
        .use(dollarmath_plugin, double_inline=True, allow_digits=False)
        .use(texmath_plugin, delimiters="brackets")
        .use(amsmath_plugin)
    )
    # Models put `$$…$$` on the line right after "The formula is:" with no
    # blank line between; the plugins register their block rules without
    # `alt`, so that `$$` line stays inside the paragraph as inline math.
    for rule in md.block.ruler.__rules__:
        if rule.name.startswith("math_block"):
            rule.alt = list(_PARAGRAPH_INTERRUPTERS)
            rule.fn = _probe_safe(rule.fn)
    md.block.ruler.__cache__ = None
    return md


def _probe_safe(rule: Callable) -> Callable:
    """A terminator probe (`silent=True`) must only answer "would this line
    start a block?"; dollarmath's rule emits tokens regardless, so run the
    probe against scratch state."""

    def probe_safe(state: StateBlock, start: int, end: int, silent: bool) -> bool:
        if not silent:
            return rule(state, start, end, False) or _open_dollar_block(
                state, start, end
            )
        tokens, line = state.tokens, state.line
        state.tokens = []
        try:
            return rule(state, start, end, False) or _open_dollar_block(
                state, start, end
            )
        finally:
            state.tokens, state.line = tokens, line

    return probe_safe


def _open_dollar_block(state: StateBlock, start: int, end: int) -> bool:
    """An unterminated `$$` line opens a block to the end of the input, the
    way markdown-it auto-closes a code fence: while a response streams, the
    incremental parser freezes every block before the last one, so the
    paragraph the `$$` was glued to would otherwise never be re-parsed."""
    pos = state.bMarks[start] + state.tShift[start]
    if (
        not state.src.startswith("$$", pos)
        or state.sCount[start] - state.blkIndent >= 4
    ):
        return False
    token = state.push("math_block", "math", 0)
    token.block = True
    token.content = state.src[pos + 2 : state.eMarks[end - 1]]
    token.map = [start, end]
    token.meta = {"open": True}
    state.line = end
    return True


_PROSE_RE = re.compile(r"^\d[\d.,]*\s+[A-Za-z]")


def looks_like_math(tex: str) -> bool:
    """`$6. Unterminated $` parses as math; a number followed by prose isn't."""
    return not (_PROSE_RE.match(tex) and not re.search(r"[\\^_={}]", tex))


class MathInlineMixin(MarkdownBlock):
    """Renders `math_inline` children, which the base class silently drops."""

    def _token_to_content(self, token: Token) -> Content:
        if token.children is None or not any(
            child.type in INLINE_MATH_TOKENS for child in token.children
        ):
            return super()._token_to_content(token)
        # Swap each math child for a text child carrying the placeholder run
        # (or the source as a fallback), let the base class build the content,
        # then style the runs with their image ids.
        children: list[Token] = []
        runs: list[tuple[str, Optional[latex.MathImage]]] = []
        for child in token.children:
            if child.type not in INLINE_MATH_TOKENS or not looks_like_math(
                child.content
            ):
                if child.type in INLINE_MATH_TOKENS:
                    child = Token("text", "", 0, content=f"${child.content}$")
                children.append(child)
                continue
            image = latex.render(child.content, display=False)
            text = (
                image.placeholder_row(0)
                if image is not None
                else re.sub(r"\s+", " ", f"${child.content}$")
            )
            substitute = Token("text", "", 0, content=text)
            children.append(substitute)
            runs.append((text, image))
        patched = copy.copy(token)
        patched.children = children
        content = super()._token_to_content(patched)
        plain = content.plain
        spans = list(content.spans)
        position = 0
        for text, image in runs:
            start = plain.find(text, position)
            position = start + len(text)
            if image is not None:
                spans.append(Span(start, position, image.style))
        return Content(plain, spans=spans)


class MarkdownMath(MarkdownBlock):
    """Display math: one placeholder line per image row, centered."""

    DEFAULT_CSS = """
    MarkdownMath {
        width: 1fr;
        height: auto;
        margin: 0 0 1 0;
        text-align: center;
    }
    """

    def __init__(
        self, markdown: Markdown, token: Token, source: str, *, open: bool = False
    ) -> None:
        super().__init__(markdown, token)
        self.tex = source
        self.open = open  # still streaming: show the source, render once closed
        self._fit_cols: int | None = None
        self._build()

    def _build(self) -> None:
        image = (
            None
            if self.open
            else latex.render(self.tex, display=True, max_cols=self._fit_cols)
        )
        if image is None:
            self.set_content(
                Content(f"$$\n{self.tex}\n$$" if not self.open else f"$$\n{self.tex}")
            )
            return
        lines = [image.placeholder_row(row) for row in range(image.rows)]
        spans: list[Span] = []
        offset = 0
        for line in lines:
            spans.append(Span(offset, offset + len(line), image.style))
            offset += len(line) + 1
        self.set_content(Content("\n".join(lines), spans=spans))

    def _on_resize(self, event: Resize) -> None:
        # Wider than the pane would wrap the placeholder rows; re-render to fit.
        width = event.size.width
        if width > 0 and width != self._fit_cols:
            self._fit_cols = width
            self._build()


def _with_inline_math(block_class: type[MarkdownBlock]) -> type[MarkdownBlock]:
    # The block class goes first: Textual inherits DEFAULT_CSS along the
    # *first* base at each level, and the mixin still precedes MarkdownBlock.
    subclass = type(
        block_class.__name__, (block_class, MathInlineMixin), {"__module__": __name__}
    )
    assert issubclass(subclass, MarkdownBlock)
    return subclass


class OiMarkdown(Markdown):
    """`Markdown` whose blocks render LaTeX math (see `tui.latex`)."""

    BLOCKS = {name: _with_inline_math(cls) for name, cls in Markdown.BLOCKS.items()}

    def __init__(self, markdown: str | None = None, **kwargs) -> None:
        super().__init__(markdown, parser_factory=make_parser, **kwargs)

    def unhandled_token(self, token: Token) -> MarkdownBlock | None:
        if token.type in BLOCK_MATH_TOKENS:
            return MarkdownMath(
                self, token, token.content.strip(), open=bool(token.meta.get("open"))
            )
        return None

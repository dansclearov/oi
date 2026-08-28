"""LaTeX math rendering: preprocessing, layout, TGP transmission, markdown hooks."""

from __future__ import annotations

import pytest
import asyncio

from textual.app import App, ComposeResult

from oi.tui import latex
from oi.tui.latex import (
    MathError,
    MathImage,
    TerminalMetrics,
    _displaystyle,
    _Env,
    _split_envs,
    _split_top,
    _textstyle,
    parse_osc_color,
    preprocess,
)
from oi.tui.markdown import MarkdownMath, OiMarkdown, looks_like_math

METRICS = TerminalMetrics(cell_width=10, cell_height=22, foreground=(220, 220, 220))


@pytest.fixture
def sender(monkeypatch):
    """Enable rendering against a recording sender; disabled again afterwards."""
    pytest.importorskip("matplotlib")
    sent: list[tuple[dict, str | None]] = []
    latex.configure(
        METRICS, sender=lambda control, payload: sent.append((control, payload))
    )
    yield sent
    latex.configure(None)


@pytest.fixture
def disabled():
    latex.configure(None)
    yield
    latex.configure(None)


# --- preprocessing -----------------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        (r"a \le b \ge c \ne d", r"a \leq b \geq c \neq d"),
        (r"\left( x \right)", r"\left( x \right)"),  # \le must not eat \left
        (r"\tfrac{1}{2} \dfrac{3}{4}", r"\frac{1}{2} \frac{3}{4}"),
        (r"\boxed{x}", "{x}"),
        (r"\bigl( x \Bigr)", "( x )"),
        (r"a \bmod n", r"a \;\mathrm{mod}\; n"),
        (r"\xrightarrow{f}", r"\overset{f}{\longrightarrow}"),
        (r"\lVert v \rVert", r"\| v \|"),
        (r"\textbf{x} \operatorname*{arg}", r"\mathbf{x} \operatorname{arg}"),
        (r"x \label{eq:1} \nonumber", "x  "),
        (r"\textcolor{red}{x}", "{x}"),
    ],
)
def test_preprocess_aliases(source, expected):
    assert preprocess(source) == expected


def test_textstyle_moves_limits_beside_big_operators():
    assert _textstyle(r"\sum_{i=1}^n x_i") == r"{\Sigma}_{i=1}^n x_i"
    assert _textstyle(r"\int_0^1") == r"\int_0^1"
    assert _textstyle(r"\lim_{x \to 0}") == r"{\lim}_{x \to 0}"
    assert _textstyle(r"\summary") == r"\summary"


def test_textstyle_turns_fractions_into_case_fractions():
    assert _textstyle(r"\frac{a}{b}") == r"{}^{a}\!/\!{}_{b}"
    assert _textstyle(r"\frac{x^{2}}{2}") == r"{}^{x^{2}}\!/\!{}_{2}"
    assert _textstyle(r"\frac{\frac{1}{2}}{3}") == r"{}^{{}^{1}\!/\!{}_{2}}\!/\!{}_{3}"


def test_looks_like_math():
    assert not looks_like_math("6. Unterminated ")
    assert not looks_like_math("5 and ")
    assert looks_like_math("5 \\cdot x")
    assert looks_like_math("x = 5")
    assert looks_like_math("6")


def test_newlines_inside_math_are_whitespace(sender):
    _, cols, rows = latex.rasterize("a +\nb", METRICS, display=True)
    assert cols > 0 and rows > 0


def test_preprocess_undoes_doubled_backslash_before_commands():
    assert preprocess(r"R(\\theta) \\ x") == r"R(\theta) \\ x"


def test_displaystyle_promotes_top_level_fractions_only():
    assert _displaystyle(r"\frac{a}{\frac{b}{c}}") == r"\dfrac{a}{\frac{b}{c}}"
    assert (
        _displaystyle(r"\frac{1}{\sum_j x_j} \sum_i")
        == r"\dfrac{1}{{\sum}_j x_j} \sum_i"
    )
    assert _displaystyle(r"x^{\frac12}") == r"x^{\frac12}"
    assert _displaystyle(r"\fracx") == r"\fracx"


def test_split_top_respects_braces_and_environments():
    assert _split_top(r"a & b", "&") == ["a ", " b"]
    assert _split_top(r"{a & b} & c", "&") == ["{a & b} ", " c"]
    assert _split_top(r"\begin{cases} a & b \\ c \end{cases} \\ d", r"\\") == [
        r"\begin{cases} a & b \\ c \end{cases} ",
        " d",
    ]
    assert _split_top(r"a \& b", "&") == [r"a \& b"]


def test_split_envs():
    text, env, rest = _split_envs(r"A = \begin{pmatrix} 1 \\ 2 \end{pmatrix} + B")
    assert text == "A = "
    assert isinstance(env, _Env)
    assert (env.name, env.body) == ("pmatrix", " 1 \\\\ 2 ")
    assert rest == " + B"
    (array,) = _split_envs(r"\begin{array}{cc} a & b \end{array}")
    assert isinstance(array, _Env)
    assert array.colspec == "cc"
    with pytest.raises(MathError):
        _split_envs(r"\begin{align} x")


def test_parse_osc_color():
    assert parse_osc_color("\x1b]10;rgb:ffff/8080/0000\x1b\\") == (255, 128, 0)
    assert parse_osc_color("\x1b]10;rgb:ff/80/00\x07") == (255, 128, 0)
    assert parse_osc_color("garbage") is None


# --- rasterization -----------------------------------------------------------


def test_inline_math_is_one_cell_row(sender):
    image, cols, rows = latex.rasterize("E = mc^2", METRICS, display=False)
    assert rows == 1
    assert image.size == (cols * 10, 22)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0  # transparent background


def test_display_math_is_cell_exact_and_capped(sender):
    tex = r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}"
    image, cols, rows = latex.rasterize(tex, METRICS, display=True)
    assert rows > 1
    assert image.size == (cols * 10, rows * 22)
    capped, capped_cols, _ = latex.rasterize(
        tex, METRICS, display=True, max_cols=cols // 2
    )
    assert capped_cols == cols // 2
    assert capped.size[0] == capped_cols * 10


@pytest.mark.parametrize(
    "tex",
    [
        r"\begin{align} f(x) &= x^2 \\ &= y \end{align}",
        r"A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}",
        r"\left[ \begin{array}{cc} a & b \\ c & d \end{array} \right] v",
        r"f(x) = \begin{cases} 1 & x > 0 \\ 0 & \text{otherwise} \end{cases}",
        r"a = b \\ c = d",
        r"\text{softmax}(z)_i = \frac{e^{z_i}}{\sum_j e^{z_j}}",
    ],
)
def test_environments_render(sender, tex):
    image, cols, rows = latex.rasterize(tex, METRICS, display=True)
    assert cols > 0 and rows > 0


def test_unrenderable_raises(sender):
    with pytest.raises(MathError):
        latex.rasterize(
            r"\begin{tikzpicture} x \end{tikzpicture}", METRICS, display=True
        )
    with pytest.raises(MathError):
        latex.rasterize(r"\frac{a}", METRICS, display=False)


# --- transmission + cache ----------------------------------------------------


def test_render_transmits_once_and_caches(sender):
    first = latex.render("x^2", display=False)
    assert first is not None
    controls = [control for control, _ in sender]
    assert controls[0]["f"] == 100 and controls[0]["q"] == 2
    assert controls[-1] == {
        "a": "p",
        "i": first.image_id,
        "c": first.cols,
        "r": 1,
        "U": 1,
        "q": 2,
    }
    assert all(payload for _, payload in sender[:-1])
    sender.clear()
    assert latex.render("x^2", display=False) is first
    assert latex.render(" x^2 ", display=False) is first
    assert sender == []


def test_render_returns_none_for_bad_tex_or_when_disabled(sender):
    assert latex.render(r"\frac{", display=False) is None
    latex.configure(None)
    assert latex.render("x", display=False) is None


def test_placeholder_row_and_style():
    image = MathImage(image_id=0x01A2B3C4, cols=3, rows=2)
    row = image.placeholder_row(1)
    assert len(row) == 3 * 4
    assert row[0] == latex.PLACEHOLDER
    assert row[1] == chr(latex.DIACRITICS[1])  # row 1
    assert row[6] == chr(latex.DIACRITICS[1])  # column 1
    assert row[3] == chr(latex.DIACRITICS[0x01])  # id high byte
    assert image.style.foreground is not None
    assert image.style.foreground.rgb == (0xA2, 0xB3, 0xC4)


# --- markdown ----------------------------------------------------------------


class MarkdownApp(App[None]):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source

    def compose(self) -> ComposeResult:
        yield OiMarkdown(self.source)


def test_inline_math_becomes_styled_placeholders(sender):
    async def scenario() -> None:
        async with MarkdownApp("Energy $E = mc^2$ here.").run_test() as pilot:
            paragraph = pilot.app.query_one("MarkdownParagraph")
            content = paragraph._content
            image = latex.render("E = mc^2", display=False)
            assert image is not None
            run = image.placeholder_row(0)
            assert content.plain == f"Energy {run} here."
            start = content.plain.index(run)
            assert any(
                span.start == start
                and span.end == start + len(run)
                and span.style == image.style
                for span in content.spans
            )

    asyncio.run(scenario())


def test_display_math_block(sender):
    async def scenario() -> None:
        async with MarkdownApp(
            "Before\n\n$$\nx = \\frac{1}{2}\n$$\n\nAfter"
        ).run_test() as pilot:
            block = pilot.app.query_one(MarkdownMath)
            image = latex.render(r"x = \frac{1}{2}", display=True)
            assert image is not None
            lines = block._content.plain.split("\n")
            assert lines == [image.placeholder_row(r) for r in range(image.rows)]

    asyncio.run(scenario())


def test_bracket_delimiters_and_amsmath(sender):
    async def scenario() -> None:
        source = "Inline \\(a+b\\) and\n\n\\[ y = x \\]\n\n\\begin{align}a &= b\\\\c &= d\\end{align}"
        async with MarkdownApp(source).run_test() as pilot:
            assert len(pilot.app.query(MarkdownMath)) == 2
            assert (
                latex.PLACEHOLDER
                in pilot.app.query_one("MarkdownParagraph")._content.plain
            )

    asyncio.run(scenario())


def test_source_shown_when_disabled(disabled):
    async def scenario() -> None:
        async with MarkdownApp("Energy $E = mc^2$\n\n$$\nx^2\n$$").run_test() as pilot:
            assert (
                pilot.app.query_one("MarkdownParagraph")._content.plain
                == "Energy $E = mc^2$"
            )
            assert pilot.app.query_one(MarkdownMath)._content.plain == "$$\nx^2\n$$"

    asyncio.run(scenario())


def test_currency_is_not_math(disabled):
    async def scenario() -> None:
        async with MarkdownApp("It costs $5 and $6 today.").run_test() as pilot:
            assert (
                pilot.app.query_one("MarkdownParagraph")._content.plain
                == "It costs $5 and $6 today."
            )

    asyncio.run(scenario())


def test_math_blocks_interrupt_paragraphs():
    from oi.tui.markdown import make_parser

    md = make_parser()
    types = [t.type for t in md.parse("Theorem:\n$$a^2 + b^2 = c^2$$\nnext")]
    assert types.count("math_block") == 1
    assert types == [
        "paragraph_open",
        "inline",
        "paragraph_close",
        "math_block",
        "paragraph_open",
        "inline",
        "paragraph_close",
    ]
    types = [
        t.type
        for t in md.parse("A matrix:\n$$\n\\begin{pmatrix} a \\\\ b \\end{pmatrix}\n$$")
    ]
    assert types.count("math_block") == 1
    types = [t.type for t in md.parse("Bracket:\n\\[ x \\]\nafter")]
    assert types.count("math_block") == 1


def test_unterminated_dollar_block_is_open():
    from oi.tui.markdown import make_parser

    tokens = make_parser().parse("Text:\n$$\n\\begin{pmatrix} a \\\\ b")
    assert [t.type for t in tokens] == [
        "paragraph_open",
        "inline",
        "paragraph_close",
        "math_block",
    ]
    assert tokens[-1].meta == {"open": True}
    assert tokens[-1].content.strip() == "\\begin{pmatrix} a \\\\ b"


def test_streamed_display_block_after_text_line(sender):
    from textual.widgets import Markdown

    source = "A matrix:\n$$\n\\begin{pmatrix} a \\\\ b \\end{pmatrix}\n$$\n\nAfter."

    async def scenario() -> None:
        async with MarkdownApp("").run_test() as pilot:
            md = pilot.app.query_one(OiMarkdown)
            stream = Markdown.get_stream(md)
            for i in range(0, len(source), 7):
                await stream.write(source[i : i + 7])
                await pilot.pause()
            await stream.stop()
            await pilot.pause()
            blocks = list(pilot.app.query(MarkdownMath))
            assert len(blocks) == 1 and not blocks[0].open
            assert blocks[0].tex == "\\begin{pmatrix} a \\\\ b \\end{pmatrix}"
            assert [p._content.plain for p in pilot.app.query("MarkdownParagraph")] == [
                "A matrix:",
                "After.",
            ]

    asyncio.run(scenario())


def test_latex_fence_becomes_display_math_when_it_renders(sender):
    from oi.tui.markdown import make_parser

    md = make_parser()
    closed = "```latex\nx = \\frac{1}{2}\n```\n"
    assert [t.type for t in md.parse(closed)] == ["math_block"]
    assert [t.type for t in md.parse("```tex\nx^2\n```")] == ["math_block"]
    assert [t.type for t in md.parse("```latex\nx = \\frac{1}{2}\n")] == [
        "fence"
    ]  # open
    assert [t.type for t in md.parse("```latex\n\\frac{\n```")] == ["fence"]  # bad
    assert [t.type for t in md.parse("```python\nx = 1\n```")] == ["fence"]
    latex.configure(None)
    assert [t.type for t in md.parse(closed)] == ["fence"]

"""Yomitan lookups: response flattening, pointer-to-text mapping, popup."""

import asyncio
import json
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from textual.widgets import Static
from textual.widgets._markdown import MarkdownParagraph

from oi.config.settings import load_user_config
from oi.tui.app import ChatInput, ChatLog
from oi.tui.lookup import (
    BOLD,
    DIM,
    HOVER_DELAY,
    POPUP_MAX_HEIGHT,
    POPUP_MAX_WIDTH,
    Entry,
    LookupPopup,
    LookupResult,
    cell_to_index,
    flatten_content,
    is_lookup_char,
    parse_term_entries,
    render_entries,
    text_cells,
)
from tests.unit.tui.test_app import _make_app

FIXTURE = Path(__file__).parents[2] / "fixtures" / "yomitan_term_entries.json"
JA_TEXT = "今日は分かりました。Hello"


def _fixture_result() -> LookupResult:
    return parse_term_entries(json.loads(FIXTURE.read_text()))


# --- pure helpers -------------------------------------------------------------


def test_cell_to_index_counts_double_width_characters():
    text = "ab日本語"
    assert cell_to_index(text, 0) == 0
    assert cell_to_index(text, 2) == 2  # 日 starts at cell 2
    assert cell_to_index(text, 3) == 2  # ...and covers cell 3
    assert cell_to_index(text, 4) == 3
    assert cell_to_index(text, 8) == len(text)


def test_lookup_chars_are_kana_kanji_and_hangul_only():
    assert all(is_lookup_char(c) for c in "分かるカタカナー々한")
    assert not any(is_lookup_char(c) for c in "Hello 123 。、!")


def test_flatten_content_lists_breaks_and_skips():
    tree = [
        {"tag": "span", "style": {"marginRight": "0.25em"}, "content": "tag"},
        {"tag": "span", "content": "next"},
        {"tag": "br"},
        {
            "tag": "ol",
            "content": [
                {
                    "tag": "li",
                    "content": [
                        {
                            "tag": "ul",
                            "data": {"content": "glossary"},
                            "content": [
                                {"tag": "li", "content": "to see"},
                                {"tag": "li", "content": "to look"},
                            ],
                        },
                        {
                            "tag": "div",
                            "data": {"content": "attribution"},
                            "content": "x",
                        },
                    ],
                },
                {
                    "tag": "li",
                    "style": {"listStyleType": '"②"'},
                    "content": [
                        {
                            "tag": "ruby",
                            "content": ["見", {"tag": "rt", "content": "み"}],
                        },
                        "る",
                        {"tag": "img", "path": "a.png"},
                    ],
                },
            ],
        },
    ]
    lines = flatten_content(tree)
    assert [line.plain for line in lines] == [
        "tag next",
        "1. to see; to look",
        "2. 見る",
    ]


def _plain(lines):
    return [line.plain for line in lines]


def test_flatten_content_tables_align_columns_with_bold_headers():
    tree = {
        "tag": "table",
        "content": [
            {
                "tag": "tr",
                "content": [
                    {"tag": "th"},
                    {"tag": "th", "content": "机"},
                    {"tag": "th", "content": "案"},
                ],
            },
            {
                "tag": "tr",
                "content": [
                    {"tag": "th", "content": "つくえ"},
                    {"tag": "td", "content": {"tag": "div", "content": "△"}},
                    {"tag": "td", "content": {"tag": "div", "content": "旧"}},
                ],
            },
            {
                "tag": "tr",
                "content": [
                    {"tag": "th", "content": "つき"},
                    {"tag": "td", "content": "古"},
                    {"tag": "td"},
                ],
            },
        ],
    }
    lines = flatten_content(tree)
    assert _plain(lines) == ["        机  案", "つくえ  △   旧", "つき    古"]
    assert lines[1].spans[0].style == BOLD and lines[1].spans[0].end == 3


def test_flatten_content_dims_chips_and_small_print_and_joins_labelled_asides():
    tree = [
        {
            "tag": "span",
            "style": {"backgroundColor": "#565656", "marginRight": "0.25em"},
            "content": "noun",
        },
        {"tag": "div", "content": "desk"},
        {
            "tag": "div",
            "data": {"content": "sense-note"},
            "content": [
                {"tag": "div", "style": {"fontSize": "0.8em"}, "content": "Note"},
                {"tag": "div", "content": "occ. 奔る"},
            ],
        },
        "つくえ【机】\n読書に用いる台。",
        {"tag": "span", "style": {"fontSize": "small"}, "content": "(づくえ)"},
    ]
    lines = flatten_content(tree)
    assert _plain(lines) == [
        "noun",
        "desk",
        "Note occ. 奔る",
        "つくえ【机】",
        "読書に用いる台。(づくえ)",
    ]
    assert lines[0].spans[0].style == DIM
    assert lines[1].spans == []
    assert lines[2].spans[0].style == DIM
    assert lines[4].spans[-1].style == DIM and lines[4].spans[-1].start == len(
        "読書に用いる台。"
    )


def test_parse_fixture_distills_headwords_frequencies_and_glossary():
    result = _fixture_result()
    assert result.length == 3
    first = result.entries[0]
    assert first.headwords == [("分かる", "わかる")]
    # Two JPDB rows collapse to the first; BCCWJ has no display value.
    assert first.frequencies == ["455㋕", "85"]
    assert first.inflections == []
    definition = first.definitions[0]
    assert definition.dictionary.startswith("Jitendex")
    assert _plain(definition.lines) == [
        "＊ 5-dan intransitive kana",
        "  1. to understand; to comprehend; to grasp; to see; to get; to follow",
        "  2. to become clear; to be known; to be discovered; to be realized;"
        " to be realised; to be found out",
        "＊ interjection",
        "  3. I know!; I think so too!",
        "＊ forms 分かる, 解る, 判る, 分る",
    ]


def test_parse_reads_inflection_chain_and_pitch():
    payload = {
        "originalTextLength": 6,
        "dictionaryEntries": [
            {
                "headwords": [{"term": "食べる", "reading": "たべる", "tags": []}],
                "inflectionRuleChainCandidates": [
                    {"inflectionRules": [{"name": "potential"}, {"name": "negative"}]}
                ],
                "pronunciations": [
                    {
                        "pronunciations": [
                            {"type": "pitch-accent", "positions": 2},
                            {"type": "pitch-accent", "positions": 2},
                        ]
                    }
                ],
                "definitions": [
                    {
                        "dictionary": "JMdict",
                        "tags": [{"name": "v1"}],
                        "entries": ["to eat", "to live on"],
                    }
                ],
            }
        ],
    }
    entry = parse_term_entries(payload).entries[0]
    assert entry.inflections == ["potential", "negative"]
    assert entry.pitches == [2]
    assert entry.definitions[0].tags == ["v1"]
    assert _plain(entry.definitions[0].lines) == ["1. to eat", "2. to live on"]
    plain = render_entries([entry]).plain
    assert plain.startswith("食べる【たべる】 [2]")
    assert "potential « negative" in plain
    assert "JMdict  v1\n  1. to eat" in plain


# --- popup behaviour ----------------------------------------------------------


class FakeYomitanClient:
    base_url = "http://fake:1"

    def __init__(self, result=None, reachable=True):
        self.result = result
        self.reachable = reachable
        self.requests = []

    async def term_entries(self, text):
        self.requests.append(text)
        return self.result if self.reachable else None


def _japanese_app(tmp_path, client, *, hover=True):
    app, chat, ctx = _make_app(tmp_path)
    ctx.config.yomitan_hover = hover
    chat.messages.extend(
        [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content=JA_TEXT)]),
        ]
    )
    app._lookup_client = client
    return app, ctx


async def _settle(pilot):
    await pilot.pause(HOVER_DELAY + 0.1)
    await pilot.pause()
    await pilot.pause()


def _popup_text(app) -> str:
    return str(app.query_one(LookupPopup).query_one(Static).render())


def test_hover_looks_up_the_word_under_the_pointer(tmp_path):
    async def scenario():
        client = FakeYomitanClient(_fixture_result())
        app, _ = _japanese_app(tmp_path, client)
        async with app.run_test() as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)
            popup = app.query_one(LookupPopup)

            # Cell 6 is 分 (three double-width characters in).
            await pilot.hover(paragraph, (6, 0))
            await _settle(pilot)
            assert client.requests == ["分かりました。Hello"]
            assert popup.display
            assert "分かる【わかる】" in _popup_text(app)
            # Anchored under the word: one row below its line, at its column.
            content = paragraph.content_region
            assert popup.region.y == content.y + 1
            assert popup.region.x == content.x + 6

            # Still on the matched 分かり (3 chars = 6 cells): no re-lookup.
            await pilot.hover(paragraph, (11, 0))
            await _settle(pilot)
            assert len(client.requests) == 1
            assert popup.display

            # Onto the popup itself: stays (so it can be scrolled).
            await pilot.hover(popup, (2, 1))
            await _settle(pilot)
            assert popup.display

            # Off the word onto Latin text: hides, and nothing to look up.
            await pilot.hover(paragraph, (20, 0))
            await pilot.pause()
            assert not popup.display
            await _settle(pilot)
            assert len(client.requests) == 1

    asyncio.run(scenario())


def test_click_looks_up_even_with_hover_off(tmp_path):
    async def scenario():
        client = FakeYomitanClient(_fixture_result())
        app, _ = _japanese_app(tmp_path, client, hover=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)
            popup = app.query_one(LookupPopup)

            await pilot.hover(paragraph, (6, 0))
            await _settle(pilot)
            assert client.requests == []
            assert not popup.display

            await pilot.click(paragraph, (6, 0))
            await _settle(pilot)
            assert client.requests == ["分かりました。Hello"]
            assert popup.display

            # Esc dismisses it (the input keeps focus throughout).
            await pilot.press("escape")
            await pilot.pause()
            assert not popup.display
            assert app.query_one("#input", ChatInput).has_focus

    asyncio.run(scenario())


def test_scroll_and_empty_results_hide_the_popup(tmp_path):
    async def scenario():
        client = FakeYomitanClient(_fixture_result())
        app, _ = _japanese_app(tmp_path, client)
        async with app.run_test() as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)
            popup = app.query_one(LookupPopup)

            await pilot.hover(paragraph, (6, 0))
            await _settle(pilot)
            assert popup.display

            app.query_one(ChatLog).post_message(ChatLog.Scrolled())
            await pilot.pause()
            assert not popup.display

            client.result = LookupResult(length=0, entries=[])
            await pilot.click(paragraph, (0, 0))
            await _settle(pilot)
            assert client.requests[-1] == JA_TEXT
            assert not popup.display

    asyncio.run(scenario())


def test_click_with_no_api_flashes_a_hint(tmp_path):
    async def scenario():
        client = FakeYomitanClient(reachable=False)
        app, _ = _japanese_app(tmp_path, client)
        async with app.run_test() as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)

            # Hovering stays quiet; the hint would nag on every word otherwise.
            await pilot.hover(paragraph, (6, 0))
            await _settle(pilot)
            assert "yomitan" not in str(app.query_one("#hint", Static).render())

            await pilot.click(paragraph, (6, 0))
            await _settle(pilot)
            hint = str(app.query_one("#hint", Static).render())
            assert "yomitan api not reachable at http://fake:1" in hint
            assert client.requests == []

    asyncio.run(scenario())


def test_yomitan_command_toggles_hover_live_and_persists(tmp_path):
    async def scenario():
        app, ctx = _japanese_app(tmp_path, FakeYomitanClient())
        async with app.run_test() as pilot:
            popup = app.query_one(LookupPopup)
            assert popup.hover is True

            app.query_one("#input", ChatInput).insert("/yomitan")
            await pilot.press("enter")
            await pilot.pause()

            assert ctx.config.yomitan_hover is False
            assert popup.hover is False
            assert load_user_config()["yomitan_hover"] is False

    asyncio.run(scenario())


def test_popup_is_as_wide_as_its_longest_line_and_wraps_past_the_cap(tmp_path):
    async def scenario():
        client = FakeYomitanClient(_fixture_result())
        app, _ = _japanese_app(tmp_path, client)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)
            popup = app.query_one(LookupPopup)

            await pilot.click(paragraph, (6, 0))
            await _settle(pilot)
            # Jitendex glosses run past the cap: the body wraps inside it
            # rather than being clipped (an auto width would clip).
            assert popup.size.width == POPUP_MAX_WIDTH
            body = popup.query_one(Static)
            assert body.size.width == POPUP_MAX_WIDTH
            assert body.size.height > len(_popup_text(app).splitlines())

            client.result = LookupResult(
                3, [Entry([("分かる", "わかる")], [], [], ["12"], [], [])]
            )
            await pilot.click(paragraph, (0, 0))
            await pilot.click(paragraph, (6, 0))
            await _settle(pilot)
            assert popup.size.width == text_cells("分かる【わかる】  12")

    asyncio.run(scenario())


def test_popup_height_is_capped_by_the_room_beside_the_word(tmp_path):
    async def scenario():
        client = FakeYomitanClient(_fixture_result())
        app, _ = _japanese_app(tmp_path, client)
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            paragraph = app.query_one(MarkdownParagraph)
            popup = app.query_one(LookupPopup)

            await pilot.click(paragraph, (6, 0))
            await _settle(pilot)
            word_y = paragraph.content_region.y
            # The entry is taller than the screen; the popup takes only the
            # rows below the word (the roomier side) instead of covering it.
            assert popup.region.y == word_y + 1
            assert popup.region.height == 12 - word_y - 2
            assert popup.region.height < POPUP_MAX_HEIGHT

    asyncio.run(scenario())


def test_render_entries_separates_entries_with_a_blank_line():
    entries = [
        Entry([("一", "いち")], [], [], [], [], []),
        Entry([("二", "に"), ("弐", "に")], ["P"], [], ["10"], [1], []),
    ]
    plain = render_entries(entries).plain
    assert plain == "一【いち】\n\n二【に】 · 弐【に】 [1]  P  10"

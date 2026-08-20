from datetime import datetime, timedelta
from unittest.mock import Mock

from rich.cells import cell_len
from rich.text import Text

from oi.constants import MAX_TITLE_LENGTH
from oi.core.session import ChatMetadata
from oi.text import truncate_to_cells
from oi.ui.chat_selector import ChatSelector


def _chat_metadata(chat_id: str, minutes_ago: int = 0) -> ChatMetadata:
    now = datetime.now() - timedelta(minutes=minutes_ago)
    return ChatMetadata(
        id=chat_id,
        title=f"Chat {chat_id}",
        created_at=now,
        updated_at=now,
        model="sonnet",
        message_count=2,
    )


def test_get_page_chats_returns_expected_slice():
    selector = ChatSelector(Mock())
    chats = [_chat_metadata(str(i)) for i in range(25)]

    page = selector._get_page_chats(chats, current_page=1, page_size=10)

    assert [chat.id for chat in page] == [str(i) for i in range(10, 20)]


def test_clamp_selection_state_clamps_page_and_index():
    selector = ChatSelector(Mock())
    chats = [_chat_metadata(str(i)) for i in range(12)]

    current_page, selected_index, total_pages = selector._clamp_selection_state(
        chats,
        page_size=10,
        current_page=5,
        selected_index=50,
    )

    assert total_pages == 2
    assert current_page == 1
    assert selected_index == 1


def test_refresh_chat_list_removes_deleted_id():
    selector = ChatSelector(Mock())
    chats = [_chat_metadata("a"), _chat_metadata("b"), _chat_metadata("c")]

    refreshed = selector._refresh_chat_list(chats, "b")

    assert [chat.id for chat in refreshed] == ["a", "c"]


def test_filter_chats_returns_only_bookmarked_entries():
    selector = ChatSelector(Mock())
    chats = [_chat_metadata("a"), _chat_metadata("b"), _chat_metadata("c")]
    chats[1].bookmarked = True

    filtered = selector._filter_chats(chats, bookmarked_only=True)

    assert [chat.id for chat in filtered] == ["b"]


def _row_cell_width(title: str, width: int, *, selected: bool = False) -> int:
    selector = ChatSelector(Mock())
    chat = _chat_metadata("a")
    chat.title = title
    row = selector._format_chat_row(
        chat, 1, selected=selected, search_mode=False, width=width
    )
    return Text.from_markup(row).cell_len


def test_format_chat_row_fits_width_with_wide_characters():
    """Wide (CJK) titles take two cells per character; the row must still fit."""
    kanji = "日本語の漢字がたくさん入っているとても長いタイトル"
    for width in (100, 80, 60, 40):
        assert _row_cell_width(kanji, width) <= width
        assert _row_cell_width(kanji, width, selected=True) <= width
        assert _row_cell_width(f"mixed {kanji} tail", width) <= width


def test_format_chat_row_pads_short_wide_title_to_title_column():
    """A short title is padded in cells, so the meta column stays aligned."""
    assert _row_cell_width("短い漢字", 100) == _row_cell_width("short", 100)


def _row(title: str, width: int) -> str:
    selector = ChatSelector(Mock())
    chat = _chat_metadata("a")
    chat.title = title
    return Text.from_markup(
        selector._format_chat_row(
            chat, 1, selected=False, search_mode=False, width=width
        )
    ).plain


def test_a_title_stored_at_the_cap_is_shown_whole():
    """The stored cap and the title column are both cell budgets, so a title
    oi wrote itself is never re-cut on a wide terminal, whatever its script.
    Any ellipsis on such a row is the stored one, marking the message it came
    from — not the selector running out of room."""
    for raw in ("english words " * 20, "漢字" * 100):
        stored = truncate_to_cells(raw, MAX_TITLE_LENGTH)
        assert cell_len(stored) <= MAX_TITLE_LENGTH
        assert stored in _row(stored, 160)


def test_a_title_too_wide_for_the_column_is_re_cut():
    """Narrow terminals re-cut, with no gap before the mark and no ASCII dots."""
    for title in ("漢字" * 40, "word " * 40):
        row = _row(title, 100)
        assert "…" in row and " …" not in row
        assert "..." not in row

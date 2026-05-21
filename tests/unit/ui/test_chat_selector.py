from datetime import datetime, timedelta
from unittest.mock import Mock

from oi.core.session import ChatMetadata
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

from datetime import date, datetime, timedelta

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from oi.config.settings import Config
from oi.core.chat_manager import ChatManager
from oi.core.session import Chat, ChatMetadata
from oi.core.stats import StatsCollector, current_streak, longest_streak


def _manager(tmp_path, monkeypatch) -> ChatManager:
    monkeypatch.setenv("OI_CHAT_DIR", str(tmp_path))
    return ChatManager(Config())


def _save(manager, chat_id, model, created, turns, *, bookmarked=False, thinking=False):
    """Persist a chat. `turns` = [(user, ai, in_tok, out_tok), ...]."""
    messages = []
    for user_text, ai_text, in_tok, out_tok in turns:
        messages.append(ModelRequest(parts=[UserPromptPart(user_text)]))
        parts = [TextPart(content=ai_text)]
        if thinking:
            parts.insert(0, ThinkingPart(content="hmm"))
        messages.append(
            ModelResponse(
                parts=parts,
                usage=RequestUsage(input_tokens=in_tok, output_tokens=out_tok),
            )
        )
    meta = ChatMetadata(
        id=chat_id,
        title=f"Chat {chat_id}",
        created_at=created,
        updated_at=created,
        model=model,
        message_count=0,
        bookmarked=bookmarked,
    )
    manager.save_chat(Chat(metadata=meta, messages=messages))


def test_collect_metadata_only(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    day = datetime(2026, 1, 5, 20, 0, 0)
    _save(manager, "a", "sonnet", day, [("hi", "yo", 1, 2), ("more", "ok", 1, 2)])
    _save(manager, "b", "sonnet", day, [("q", "a", 1, 2)], bookmarked=True)
    _save(manager, "c", "haiku", day + timedelta(days=2), [("x", "y", 1, 2)])

    stats = StatsCollector(manager).collect()

    assert stats.total_chats == 3
    assert stats.total_messages == 8  # 4 + 2 + 2 non-system messages
    assert stats.bookmarked == 1
    assert stats.top_model == "sonnet"
    assert stats.per_model["sonnet"].chats == 2
    assert stats.per_model["sonnet"].messages == 6
    assert stats.per_model["haiku"].messages == 2
    assert stats.first_activity == day
    assert stats.daily_counts[day.date()] == 2
    assert stats.deep is None  # cheap pass leaves deep unset


def test_collect_deep(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    day = datetime(2026, 1, 5, 20, 0, 0)
    _save(
        manager,
        "a",
        "sonnet",
        day,
        [("two words", "three words here", 10, 20)],
        thinking=True,
    )
    _save(manager, "b", "haiku", day, [("one", "a b", 5, 7)])

    stats = StatsCollector(manager).collect(deep=True)
    deep = stats.deep

    assert deep is not None
    assert deep.user_words == 3  # "two words" + "one"
    assert deep.ai_words == 5  # "three words here" + "a b"
    assert deep.thinking_responses == 1
    assert deep.biggest_chat_by_words == ("Chat a", 5)  # 2 user + 3 AI words


def test_collect_empty(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    stats = StatsCollector(manager).collect(deep=True)
    assert stats.total_chats == 0
    assert stats.top_model is None


def test_longest_streak():
    counts = {
        date(2026, 1, 1): 1,
        date(2026, 1, 2): 1,
        date(2026, 1, 3): 1,
        # gap
        date(2026, 1, 10): 1,
    }
    assert longest_streak(counts) == 3
    assert longest_streak({}) == 0


def test_current_streak():
    today = date(2026, 1, 10)
    # active today and the two prior days
    counts = {today: 1, today - timedelta(days=1): 1, today - timedelta(days=2): 1}
    assert current_streak(counts, today=today) == 3

    # idle today but active yesterday still counts
    counts2 = {today - timedelta(days=1): 1, today - timedelta(days=2): 1}
    assert current_streak(counts2, today=today) == 2

    # last activity two days ago → streak broken
    assert current_streak({today - timedelta(days=2): 1}, today=today) == 0

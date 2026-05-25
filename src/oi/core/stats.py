"""Aggregate statistics over the chat history.

`StatsCollector.collect()` runs a cheap pass over chat metadata. With
`deep=True` it also loads each transcript to count words said.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)

from oi.core.chat_manager import ChatManager
from oi.core.session import ChatMetadata


@dataclass
class ModelStat:
    """Per-model rollup derived from chat metadata."""

    chats: int = 0
    messages: int = 0


@dataclass
class DeepStats:
    """Transcript-derived stats (only populated when collect(deep=True)).

    Words come from text parts only, so they exclude thinking traces and
    web-search results — the honest measure of what was actually said.
    """

    user_words: int = 0
    ai_words: int = 0
    images: int = 0
    thinking_responses: int = 0
    biggest_chat_by_words: Optional[tuple[str, int]] = None  # (title, total words)


@dataclass
class Stats:
    """Everything `render_stats` needs, computed once up front."""

    total_chats: int = 0
    total_messages: int = 0
    bookmarked: int = 0
    smart_titled: int = 0
    first_activity: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    per_model: dict[str, ModelStat] = field(default_factory=dict)
    daily_counts: dict[date, int] = field(default_factory=dict)  # chats started/day
    by_hour: dict[int, int] = field(default_factory=dict)
    by_weekday: dict[int, int] = field(default_factory=dict)
    deep: Optional[DeepStats] = None

    @property
    def top_model(self) -> Optional[str]:
        if not self.per_model:
            return None
        return max(self.per_model.items(), key=lambda kv: kv[1].messages)[0]


class StatsCollector:
    """Walks the chat repository and aggregates statistics."""

    def __init__(self, manager: ChatManager):
        self.manager = manager

    def collect(self, *, deep: bool = False) -> Stats:
        metas = self.manager.list_chats()
        stats = Stats(total_chats=len(metas))
        if not metas:
            return stats

        deep_stats = DeepStats() if deep else None

        for meta in metas:
            stats.total_messages += meta.message_count
            stats.bookmarked += int(meta.bookmarked)
            stats.smart_titled += int(meta.smart_title_generated)

            created = meta.created_at
            day = created.date()
            stats.daily_counts[day] = stats.daily_counts.get(day, 0) + 1
            stats.by_hour[created.hour] = stats.by_hour.get(created.hour, 0) + 1
            weekday = created.weekday()
            stats.by_weekday[weekday] = stats.by_weekday.get(weekday, 0) + 1

            if stats.first_activity is None or created < stats.first_activity:
                stats.first_activity = created
            if stats.last_activity is None or meta.updated_at > stats.last_activity:
                stats.last_activity = meta.updated_at

            model_stat = stats.per_model.setdefault(meta.model, ModelStat())
            model_stat.chats += 1
            model_stat.messages += meta.message_count

            if deep_stats is not None:
                self._scan_transcript(meta, deep_stats)

        stats.deep = deep_stats
        return stats

    def _scan_transcript(self, meta: ChatMetadata, deep: DeepStats) -> None:
        """Load one chat and fold its message contents into `deep`."""
        chat = self.manager.load_chat(meta.id)
        chat_words = 0

        for message in chat.messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        words, images = _count_user_content(part.content)
                        deep.user_words += words
                        deep.images += images
                        chat_words += words
            elif isinstance(message, ModelResponse):
                has_thinking = False
                for part in message.parts:
                    if isinstance(part, TextPart) and part.content:
                        words = len(part.content.split())
                        deep.ai_words += words
                        chat_words += words
                    elif isinstance(part, ThinkingPart):
                        has_thinking = True
                if has_thinking:
                    deep.thinking_responses += 1

        if (
            deep.biggest_chat_by_words is None
            or chat_words > deep.biggest_chat_by_words[1]
        ):
            deep.biggest_chat_by_words = (meta.title, chat_words)


def _count_user_content(content: object) -> tuple[int, int]:
    """Return (word_count, image_count) for a UserPromptPart payload."""
    if isinstance(content, str):
        return len(content.split()), 0
    if not isinstance(content, (list, tuple)):
        return len(str(content).split()), 0

    words = 0
    images = 0
    for part in content:
        if isinstance(part, str):
            words += len(part.split())
        elif isinstance(part, (BinaryContent, ImageUrl)):
            images += 1
    return words, images


def longest_streak(daily: dict[date, int]) -> int:
    """Longest run of consecutive active days anywhere in the history."""
    if not daily:
        return 0
    days = sorted(daily)
    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)
    return longest


def current_streak(daily: dict[date, int], *, today: Optional[date] = None) -> int:
    """Active days counting back from today (or yesterday, if today is idle)."""
    if not daily:
        return 0
    today = today or date.today()
    if today in daily:
        cursor = today
    elif (today - timedelta(days=1)) in daily:
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    while cursor in daily:
        streak += 1
        cursor -= timedelta(days=1)
    return streak

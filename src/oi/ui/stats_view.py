"""Rich rendering for `oi stats`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from oi.core.stats import Stats, current_streak, longest_streak

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# GitHub dark-mode green ramp for activity levels 1..4. Idle days (level 0) are
# left as the terminal background rather than drawn.
HEATMAP_COLORS = ["#0e4429", "#006d32", "#26a641", "#39d353"]
BAR_WIDTH = 16
BAR_PARTIALS = "▏▎▍▌▋▊▉"  # 1/8 .. 7/8 of a cell, for fractional bar ends
HEATMAP_WEEKS = 52
READING_WPM = 200  # average adult reading speed, for the reading-time estimate


def render_stats(stats: Stats, *, console: Optional[Console] = None) -> None:
    """Render a full stats report to the console."""
    console = console or Console(highlight=False)

    if stats.total_chats == 0:
        console.print("No chats yet — start one with [bold]oi[/bold].")
        return

    _render_summary(stats, console)
    _render_activity(stats, console)
    _render_models(stats, console)
    _render_heatmap(stats.daily_counts, console)
    if stats.deep is not None:
        _render_deep(stats, console)


def _render_summary(stats: Stats, console: Console) -> None:
    span = ""
    if stats.first_activity is not None:
        days = (datetime.now() - stats.first_activity).days
        span = f" · since {stats.first_activity:%b %d, %Y} ([bold]{days:,}[/bold] days)"

    console.print(
        f"[bold]{stats.total_chats:,}[/bold] chats · "
        f"[bold]{stats.total_messages:,}[/bold] messages · "
        f"[bold]{stats.bookmarked:,}[/bold] bookmarked{span}"
    )


def _render_activity(stats: Stats, console: Console) -> None:
    parts = [
        f"streak [bold]{current_streak(stats.daily_counts)}[/bold]d "
        f"(best [bold]{longest_streak(stats.daily_counts)}[/bold]d)"
    ]
    if stats.by_hour:
        hour = max(stats.by_hour.items(), key=lambda kv: kv[1])[0]
        parts.append(f"busiest hour [bold]{hour:02d}:00[/bold]")
    if stats.by_weekday:
        weekday = max(stats.by_weekday.items(), key=lambda kv: kv[1])[0]
        parts.append(f"busiest day [bold]{WEEKDAYS[weekday]}[/bold]")
    console.print(" · ".join(parts))


def _render_models(stats: Stats, console: Console) -> None:
    console.print()
    console.print(Text("Top models", style="bold"))

    top = sorted(stats.per_model.items(), key=lambda kv: kv[1].messages, reverse=True)
    top = top[:5]
    peak = max((m.messages for _, m in top), default=0) or 1
    name_width = max((len(name) for name, _ in top), default=0)

    for name, stat in top:
        bar = _bar(stat.messages / peak * BAR_WIDTH)
        console.print(
            f"  {escape(name):<{name_width}}  {bar} "
            f"{stat.messages:,} msgs · {stat.chats:,} chats"
        )


def _reading_time(words: int) -> str:
    """Estimated time to read `words` at READING_WPM, as minutes or hours."""
    minutes = words / READING_WPM
    if minutes < 60:
        return f"{round(minutes)} min"
    return f"{round(minutes / 60):,} hours"


def _bar(width: float) -> str:
    """A fractional bar tinted along the heatmap green ramp (dark → bright), so
    longer bars climb to brighter greens, echoing the activity legend."""
    eighths = round(width * 8)
    full, rem = divmod(eighths, 8)
    cells = [f"[{HEATMAP_COLORS[min(3, i * 4 // BAR_WIDTH)]}]█[/]" for i in range(full)]
    if rem:
        shade = HEATMAP_COLORS[min(3, full * 4 // BAR_WIDTH)]
        cells.append(f"[{shade}]{BAR_PARTIALS[rem - 1]}[/]")
    return "".join(cells)


def _render_heatmap(
    daily: dict[date, int],
    console: Console,
    *,
    weeks: int = HEATMAP_WEEKS,
    today: Optional[date] = None,
) -> None:
    console.print()
    console.print(Text("Activity (past year)", style="bold"))
    if not daily:
        console.print("  (no activity yet)")
        return

    today = today or date.today()
    start = today - timedelta(days=weeks * 7)
    start -= timedelta(days=start.weekday())  # align to a Monday column
    peak = max(daily.values())
    num_weeks = (today - start).days // 7 + 1

    levels = [[0] * num_weeks for _ in range(7)]
    cursor = start
    while cursor <= today:
        week = (cursor - start).days // 7
        levels[cursor.weekday()][week] = _bucket(daily.get(cursor, 0), peak)
        cursor += timedelta(days=1)

    # Trim all-idle weeks at both ends (before the first chat, current empty
    # week) so the grid runs edge-to-edge instead of padding with grey columns.
    active = [wk for wk in range(num_weeks) if any(levels[d][wk] for d in range(7))]
    first_col, last_col = (active[0], active[-1]) if active else (0, num_weeks - 1)

    # Pack two weekdays into each character so days read as squares; idle days
    # fall through to the terminal background (see _heat_cell). The 7th day
    # (Sun) has no partner, so its lower half is always treated as idle.
    labels = ["Mon", "Wed", "Fri", "Sun"]
    for row in range(4):
        top = levels[row * 2]
        bottom = levels[row * 2 + 1] if row * 2 + 1 < 7 else None
        cells = "".join(
            _heat_cell(top[wk], bottom[wk] if bottom is not None else 0)
            for wk in range(first_col, last_col + 1)
        )
        console.print(f"  {labels[row]} {cells}")
    swatches = " " + "".join(f"[{color}]█[/]" for color in HEATMAP_COLORS)
    console.print(f"      [dim]less[/dim] {swatches} [dim]more[/dim]")


def _render_deep(stats: Stats, console: Console) -> None:
    deep = stats.deep
    assert deep is not None
    console.print()
    console.print(Text("Content", style="bold"))

    ratio = (
        f" ([bold]{deep.ai_words / deep.user_words:.0f}×[/bold] back)"
        if deep.user_words
        else ""
    )
    console.print(
        f"  words: [bold]{deep.user_words:,}[/bold] yours · "
        f"[bold]{deep.ai_words:,}[/bold] from the AI{ratio}"
    )
    console.print(
        f"  reading time: [bold]~{_reading_time(deep.ai_words)}[/bold] "
        f"[dim](at {READING_WPM} wpm)[/dim]"
    )

    if deep.biggest_chat_by_words is not None:
        title, words = deep.biggest_chat_by_words
        trimmed = title if len(title) <= 40 else title[:39] + "…"
        console.print(
            f"  biggest chat: [bold]{words:,}[/bold] words — {escape(trimmed)}"
        )

    extras = []
    if deep.images:
        extras.append(f"[bold]{deep.images:,}[/bold] images")
    if deep.thinking_responses:
        extras.append(f"[bold]{deep.thinking_responses:,}[/bold] thinking replies")
    if extras:
        console.print("  " + " · ".join(extras))


def _heat_cell(top: int, bottom: int) -> str:
    """Render one heatmap character holding two stacked days.

    Each day is a level 0-4; idle days (0) are left as the terminal background
    so only active days are drawn, via the upper/lower half blocks.
    """
    if top and bottom:
        return f"[{HEATMAP_COLORS[top - 1]} on {HEATMAP_COLORS[bottom - 1]}]▀[/]"
    if top:
        return f"[{HEATMAP_COLORS[top - 1]}]▀[/]"
    if bottom:
        return f"[{HEATMAP_COLORS[bottom - 1]}]▄[/]"
    return " "


def _bucket(count: int, peak: int) -> int:
    """Map a day's count to a shade index (0 idle, 1..4 by quartile of peak)."""
    if count <= 0:
        return 0
    frac = count / peak
    if frac <= 0.25:
        return 1
    if frac <= 0.5:
        return 2
    if frac <= 0.75:
        return 3
    return 4

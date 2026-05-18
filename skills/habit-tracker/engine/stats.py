"""Deterministic, pure streak / goal engine over a habit-tracker substrate.

NO LLM, NO I/O, NO clock reads except an explicit ``today`` you pass in (so
every result is reproducible and table-testable). Operates purely on the
generic substrate model from ``substrate.py``.

WHAT IT COMPUTES (per entity)
-----------------------------
* ``current_streak``  — consecutive *successful* civil days ending today or
  yesterday (yesterday allowed so an un-logged "today" doesn't read as broken,
  matching the v2 producer's semantics).
* ``longest_streak``  — longest run of successful days anywhere in history.
* ``goal_progress``   — for the goal interval active on ``today``: the count
  accumulated in the current period (day | week | month), the target, a
  done flag, and the (optionally exceed-capped) display count.

SUCCESS, PER DAY
----------------
For a normal entity, a civil day *succeeds* when the total ``amount`` logged
that day meets the day's required threshold:

    threshold = active_interval.per_day_target
                or active_interval.target (if period == "day")
                or 1                       (no active interval / period none)

For an **inverse** entity (success = NOT doing the thing, e.g. "no junk
food"), the day succeeds when the logged amount is *below* that threshold
(i.e. ``sum(amount) < threshold``) — a zero-amount or absent day succeeds, a
slip breaks the streak. This inverts the whole streak/goal evaluation.

GOAL CHANGES OVER TIME
----------------------
An entity may have several non-overlapping ``GoalInterval``s
(``[start, end)`` half-open civil). The interval *active* on a given civil
date is the one containing it; streak success on a day uses that day's active
interval, so a target that changed mid-history is honoured day-by-day.

TIMEZONE
--------
Events already carry a LOCAL civil ``date`` (the importer normalized the
HabitKit UTC-midnight instant via ``timezoneOffsetInMinutes`` at import time),
so streak/goal math is plain civil-date arithmetic here — the tz edge is
resolved upstream, deterministically.

ARCHIVED entities are excluded from ``active_stats`` (they still compute fine
individually if asked explicitly).
"""

from __future__ import annotations

import calendar
import importlib.util
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if "ht_substrate" in sys.modules:
    _sub = sys.modules["ht_substrate"]
else:
    _spec = importlib.util.spec_from_file_location(
        "ht_substrate", _HERE / "substrate.py"
    )
    _sub = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    # Register before exec — Py3.14 @dataclass looks up
    # sys.modules[cls.__module__] during class creation.
    sys.modules["ht_substrate"] = _sub
    _spec.loader.exec_module(_sub)  # type: ignore[union-attr]

Substrate = _sub.Substrate
Entity = _sub.Entity


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GoalProgress:
    period: str  # day | week | month | none
    period_start: str | None  # civil date the active period began (None)
    target: int | None  # required count for the period (None = no goal)
    count: int  # raw accumulated amount in the period
    display_count: int  # count, capped at target unless allow_exceed
    done: bool  # target met (always True when no goal)
    allow_exceed: bool


@dataclass(frozen=True)
class HabitStats:
    entity_id: str
    name: str
    archived: bool
    inverse: bool
    current_streak: int
    longest_streak: int
    goal: GoalProgress


# --------------------------------------------------------------------------- #
# date helpers
# --------------------------------------------------------------------------- #
def _d(s: str) -> date:
    return date.fromisoformat(s)


def _interval_active_on(intervals: list, day: date):
    """The GoalInterval whose half-open [start, end) civil range contains
    ``day``. If several do (shouldn't, but be deterministic) the latest
    ``start`` wins. None if no interval covers the day."""
    best = None
    for g in intervals:
        start = _d(g.start)
        end = _d(g.end) if g.end else None
        if start <= day and (end is None or day < end):
            if best is None or _d(g.start) >= _d(best.start):
                best = g
    return best


def _day_threshold(interval) -> int:
    """Required amount for a single day under ``interval``."""
    if interval is None:
        return 1
    if interval.per_day_target is not None:
        return max(1, int(interval.per_day_target))
    if interval.period == "day" and interval.target is not None:
        return max(1, int(interval.target))
    return 1


def _amounts_by_day(events: list) -> dict[date, int]:
    """Sum ``amount`` per civil day (multiple completions/day collapse)."""
    agg: dict[date, int] = defaultdict(int)
    for ev in events:
        agg[_d(ev.date)] += int(ev.amount)
    return dict(agg)


def _day_succeeds(
    day: date,
    by_day: dict[date, int],
    intervals: list,
    inverse: bool,
) -> bool:
    """Did ``day`` count as a success for this entity?"""
    iv = _interval_active_on(intervals, day)
    threshold = _day_threshold(iv)
    amt = by_day.get(day, 0)
    if inverse:
        # success = NOT doing it (stayed under the threshold that day).
        return amt < threshold
    return amt >= threshold


# --------------------------------------------------------------------------- #
# streaks
# --------------------------------------------------------------------------- #
def _streak_span(entity: Entity, events: list, intervals: list):
    """The civil-date range over which streaks are evaluated.

    Normal entity : from the first logged event to today.
    Inverse entity: a day with NO event is a *success*, so the span must
                    start at the active interval's start (or first event,
                    whichever is earliest) — otherwise an inverse habit with
                    zero slips would have an undefined/empty streak.
    """
    days = [_d(ev.date) for ev in events]
    starts = [_d(g.start) for g in intervals]
    candidates = days + (starts if entity.inverse else [])
    return min(candidates) if candidates else None


def current_streak(
    entity: Entity, events: list, intervals: list, *, today: date
) -> int:
    """Consecutive successful days ending today OR yesterday."""
    by_day = _amounts_by_day(events)
    start = _streak_span(entity, events, intervals)
    if start is None:
        return 0

    def succ(d: date) -> bool:
        return _day_succeeds(d, by_day, intervals, entity.inverse)

    if succ(today):
        anchor = today
    elif succ(today - timedelta(days=1)):
        anchor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    d = anchor
    while d >= start and succ(d):
        streak += 1
        d -= timedelta(days=1)
    return streak


def longest_streak(
    entity: Entity, events: list, intervals: list, *, today: date
) -> int:
    """Longest consecutive run of successful days in [span_start, today]."""
    by_day = _amounts_by_day(events)
    start = _streak_span(entity, events, intervals)
    if start is None:
        return 0
    best = run = 0
    d = start
    while d <= today:
        if _day_succeeds(d, by_day, intervals, entity.inverse):
            run += 1
            best = max(best, run)
        else:
            run = 0
        d += timedelta(days=1)
    return best


# --------------------------------------------------------------------------- #
# goal progress for the active period
# --------------------------------------------------------------------------- #
def _period_start(period: str, day: date) -> date | None:
    if period == "day":
        return day
    if period == "week":  # ISO week, Monday start
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return None


def _period_end(period: str, start: date) -> date | None:
    if period == "day":
        return start
    if period == "week":
        return start + timedelta(days=6)
    if period == "month":
        last = calendar.monthrange(start.year, start.month)[1]
        return start.replace(day=last)
    return None


def goal_progress(
    entity: Entity, events: list, intervals: list, *, today: date
) -> GoalProgress:
    """Progress toward the goal interval active on ``today``."""
    iv = _interval_active_on(intervals, today)
    if iv is None or iv.period == "none" or iv.target is None:
        return GoalProgress(
            period=(iv.period if iv else "none"),
            period_start=None,
            target=None,
            count=0,
            display_count=0,
            done=True,  # no goal => trivially satisfied
            allow_exceed=(iv.allow_exceed if iv else True),
        )
    pstart = _period_start(iv.period, today)
    pend = _period_end(iv.period, pstart)
    by_day = _amounts_by_day(events)
    count = sum(
        amt
        for d, amt in by_day.items()
        if pstart <= d <= pend
    )
    target = int(iv.target)
    done = count >= target
    display = count if iv.allow_exceed else min(count, target)
    return GoalProgress(
        period=iv.period,
        period_start=pstart.isoformat(),
        target=target,
        count=count,
        display_count=display,
        done=done,
        allow_exceed=iv.allow_exceed,
    )


# --------------------------------------------------------------------------- #
# per-entity + active aggregate
# --------------------------------------------------------------------------- #
def habit_stats(
    s: Substrate, entity_id: str, *, today: date
) -> HabitStats:
    e = s.entity(entity_id)
    if e is None:
        raise KeyError(f"no entity {entity_id!r} in substrate")
    evs = s.events_for(entity_id)
    ivs = s.intervals_for(entity_id)
    return HabitStats(
        entity_id=e.id,
        name=e.name,
        archived=e.archived,
        inverse=e.inverse,
        current_streak=current_streak(e, evs, ivs, today=today),
        longest_streak=longest_streak(e, evs, ivs, today=today),
        goal=goal_progress(e, evs, ivs, today=today),
    )


def active_stats(s: Substrate, *, today: date) -> list[HabitStats]:
    """Stats for every NON-archived entity, ordered by ``order_index`` then
    id (deterministic)."""
    ents = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id),
    )
    return [habit_stats(s, e.id, today=today) for e in ents]

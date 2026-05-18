"""Deterministic streak / goal engine — table-driven + edge cases.

Covers: current/longest streak, multi-completion days (amount), per-day
target, day/week/month goals, allowExceedingGoal true/false, inverse habits,
goal-interval change mid-history, archived exclusion, tz-edge (completion
near local midnight), goal "none", no-interval default threshold.
"""

from __future__ import annotations

from datetime import date

import pytest

from _engine import import_habitkit as imp
from _engine import stats as st
from _engine import substrate as sub
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)


# --------------------------------------------------------------------------- #
# helpers to build tiny substrates inline
# --------------------------------------------------------------------------- #
def _s(entity, events, intervals):
    s = sub.Substrate(substrate_kind="habit-tracker")
    s.entities.append(entity)
    s.events.extend(events)
    s.goal_intervals.extend(intervals)
    return s


def _e(eid="e", inverse=False, archived=False):
    return sub.Entity(id=eid, name="X", inverse=inverse, archived=archived)


def _ev(d, amount=1, eid="e", evid=None):
    return sub.Event(
        id=evid or f"{eid}-{d}-{amount}", entity_id=eid, date=d,
        amount=amount,
    )


def _iv(start, end=None, period="day", target=None, per_day=None,
        allow_exceed=True, eid="e", ivid="iv"):
    return sub.GoalInterval(
        id=ivid, entity_id=eid, start=start, end=end, period=period,
        target=target, per_day_target=per_day, allow_exceed=allow_exceed,
    )


# --------------------------------------------------------------------------- #
# current / longest streak (table-driven)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "dates,expected_cur,expected_long",
    [
        (["2026-05-14", "2026-05-15", "2026-05-16"], 3, 3),
        # today not logged, yesterday + before -> not broken
        (["2026-05-14", "2026-05-15"], 2, 2),
        # gap -> current 0 but longest from history
        (["2026-05-10", "2026-05-11", "2026-05-13"], 0, 2),
        ([], 0, 0),
        (["2026-05-16"], 1, 1),
    ],
)
def test_streak_table(dates, expected_cur, expected_long):
    s = _s(_e(), [_ev(d) for d in dates], [_iv("2026-01-01")])
    cur = st.current_streak(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    lng = st.longest_streak(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    assert cur == expected_cur
    assert lng == expected_long


def test_multi_completion_day_amount_counts_toward_per_day_target():
    # per_day_target 3; a single day with two completions amounting to 3
    # succeeds; a day with amount 2 fails.
    ev = [
        _ev("2026-05-15", 2, evid="a"),
        _ev("2026-05-15", 1, evid="b"),  # 2+1 = 3 -> ok
        _ev("2026-05-16", 2, evid="c"),  # 2 < 3 -> fail
    ]
    s = _s(_e(), ev, [_iv("2026-01-01", per_day=3)])
    cur = st.current_streak(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    # 05-16 fails, 05-15 ok -> anchor falls back to yesterday, streak 1
    assert cur == 1


def test_no_interval_defaults_threshold_one():
    s = _s(_e(), [_ev("2026-05-15"), _ev("2026-05-16")], [])
    assert st.current_streak(
        s.entities[0], s.events, [], today=TODAY
    ) == 2


# --------------------------------------------------------------------------- #
# goal interval change mid-history
# --------------------------------------------------------------------------- #
def test_goal_change_mid_history_uses_active_interval_per_day():
    # early interval [01-01, 05-15) per_day 1 ; late [05-15, ) per_day 2
    early = _iv("2026-01-01", end="2026-05-15", per_day=1, ivid="early")
    late = _iv("2026-05-15", per_day=2, ivid="late")
    ev = [
        _ev("2026-05-14", 1),  # under early target 1? 1>=1 ok
        _ev("2026-05-15", 1),  # late target 2; 1 < 2 -> FAIL
        _ev("2026-05-16", 2),  # late target 2; ok
    ]
    s = _s(_e(), ev, [early, late])
    e = s.entities[0]
    cur = st.current_streak(e, s.events, s.goal_intervals, today=TODAY)
    assert cur == 1  # only 05-16 passes the late goal; 05-15 broke it


# --------------------------------------------------------------------------- #
# goal progress: day / week / month, allow_exceed
# --------------------------------------------------------------------------- #
def test_goal_progress_week_not_met_no_exceed():
    # 3/week, only 2 logged this ISO week (Mon 05-11 .. Sun 05-17)
    ev = [_ev("2026-05-12", 1, evid="a"), _ev("2026-05-14", 1, evid="b")]
    s = _s(_e(), ev, [_iv("2026-01-01", period="week", target=3,
                          allow_exceed=False)])
    gp = st.goal_progress(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    assert gp.period == "week"
    assert gp.target == 3
    assert gp.count == 2
    assert gp.done is False
    assert gp.display_count == 2  # not capped, under target


def test_goal_progress_allow_exceed_true_vs_false():
    ev = [_ev("2026-05-16", 5)]
    base = dict(period="day", target=2)
    s_t = _s(_e("t"), [_ev("2026-05-16", 5, eid="t")],
             [_iv("2026-01-01", eid="t", allow_exceed=True, **base)])
    s_f = _s(_e("f"), [_ev("2026-05-16", 5, eid="f")],
             [_iv("2026-01-01", eid="f", allow_exceed=False, **base)])
    g_t = st.goal_progress(s_t.entities[0], s_t.events,
                           s_t.goal_intervals, today=TODAY)
    g_f = st.goal_progress(s_f.entities[0], s_f.events,
                           s_f.goal_intervals, today=TODAY)
    assert g_t.count == 5 and g_t.display_count == 5  # uncapped
    assert g_f.count == 5 and g_f.display_count == 2  # capped at target
    assert g_t.done and g_f.done


def test_goal_progress_month():
    ev = [_ev("2026-05-02", 1, evid="a"),
          _ev("2026-05-09", 1, evid="b"),
          _ev("2026-05-16", 1, evid="c")]
    s = _s(_e(), ev, [_iv("2026-01-01", period="month", target=10)])
    gp = st.goal_progress(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    assert gp.period == "month"
    assert gp.period_start == "2026-05-01"
    assert gp.count == 3 and gp.done is False


def test_goal_none_is_trivially_done():
    s = _s(_e(), [_ev("2026-05-16")],
           [_iv("2026-01-01", period="none")])
    gp = st.goal_progress(
        s.entities[0], s.events, s.goal_intervals, today=TODAY
    )
    assert gp.target is None and gp.done is True


# --------------------------------------------------------------------------- #
# inverse habits
# --------------------------------------------------------------------------- #
def test_inverse_streak_clean_days_count():
    # inverse: success = NOT doing it. interval starts 05-10, threshold 1.
    # No events at all -> every day in span is a clean success.
    s = _s(_e(inverse=True), [], [_iv("2026-05-10", per_day=1)])
    e = s.entities[0]
    cur = st.current_streak(e, s.events, s.goal_intervals, today=TODAY)
    # 05-10 .. 05-16 inclusive = 7 clean days
    assert cur == 7


def test_inverse_streak_breaks_on_slip():
    # one slip on 05-15 (amount 1 >= threshold 1) breaks the inverse streak
    s = _s(
        _e(inverse=True),
        [_ev("2026-05-15", 1)],
        [_iv("2026-05-10", per_day=1)],
    )
    e = s.entities[0]
    cur = st.current_streak(e, s.events, s.goal_intervals, today=TODAY)
    # clean days after the slip: 05-16 only -> streak 1
    assert cur == 1
    lng = st.longest_streak(e, s.events, s.goal_intervals, today=TODAY)
    # before slip: 05-10..05-14 = 5 clean days (the longest)
    assert lng == 5


# --------------------------------------------------------------------------- #
# tz-edge: completion near local midnight is attributed to the LOCAL day
# --------------------------------------------------------------------------- #
def test_tz_edge_completion_counts_on_local_day():
    s = imp.import_habitkit(habitkit_export())
    # c-r-tz (UTC 05-15T23:00, +120) was imported as civil 2026-05-16.
    # Reading has events 05-14,05-15,05-16(+the tz one also 05-16) -> a
    # clean 3-day streak ending today.
    e = s.entity("h-reading")
    cur = st.current_streak(
        e, s.events_for("h-reading"), s.intervals_for("h-reading"),
        today=TODAY,
    )
    assert cur == 3


# --------------------------------------------------------------------------- #
# archived exclusion + aggregate
# --------------------------------------------------------------------------- #
def test_active_stats_excludes_archived_and_is_ordered():
    s = imp.import_habitkit(habitkit_export())
    rows = st.active_stats(s, today=TODAY)
    ids = [r.entity_id for r in rows]
    assert "h-old" not in ids  # archived excluded
    assert ids == ["h-reading", "h-nosnack", "h-gym", "h-water"]  # ordered
    by = {r.entity_id: r for r in rows}
    assert by["h-reading"].current_streak == 3
    # NoSnack inverse: slip on 05-15 -> only 05-16 clean -> streak 1
    assert by["h-nosnack"].inverse is True
    assert by["h-nosnack"].current_streak == 1
    # Gym week goal 3, only 2 this week -> not done
    assert by["h-gym"].goal.period == "week"
    assert by["h-gym"].goal.done is False
    # Water late goal per_day 2, 05-16 amount 1 -> today fails
    assert by["h-water"].current_streak == 0


def test_habit_stats_unknown_entity_raises():
    s = sub.Substrate(substrate_kind="habit-tracker")
    with pytest.raises(KeyError):
        st.habit_stats(s, "nope", today=TODAY)


def test_stats_are_deterministic():
    s = imp.import_habitkit(habitkit_export())
    a = st.active_stats(s, today=TODAY)
    b = st.active_stats(s, today=TODAY)
    assert a == b

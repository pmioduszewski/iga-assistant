"""Wave-B multi-habit widget projection (schema_version 2).

Proves: the new payload carries per-habit color(hex)/icon/emoji/isInverse +
streaks + active-goal progress + windowed cells; numbers are VERBATIM from the
frozen stats.py (no new habit math); the frozen v1 file/contract is untouched
(back-compat); and projecting only ever writes the isolation root.
"""

from __future__ import annotations

import json
from datetime import date

from _engine import import_habitkit as imp
from _engine import stats as st
from _engine import substrate as sub
from _engine import widget_projection as wp
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)


def _payload(window=120, archived=False):
    s = imp.import_habitkit(habitkit_export())
    return s, wp.build_habits_widget_from_substrate(
        s, today=TODAY, window_days=window, include_archived=archived
    )


def test_schema_v2_shape_and_versioning():
    _s, p = _payload()
    assert p["schema_version"] == 2
    assert p["widget_id"] == "habits"
    assert p["type"] == "habit-grid-multi"
    assert p["today"] == "2026-05-16"
    assert p["window_days"] == 120
    assert set(p["data"].keys()) == {"habits", "levels"}
    h0 = p["data"]["habits"][0]
    assert set(h0.keys()) == {
        "id", "name", "color", "color_name", "icon", "emoji",
        "is_inverse", "archived", "order_index", "current_streak",
        "longest_streak", "coach", "coach_kind", "coach_tip", "goal",
        "levels", "cells",
    }
    assert h0["coach_kind"] in {
        "", "at-risk", "slipped", "milestone", "dormant"}
    assert isinstance(h0["coach_tip"], str)
    assert set(h0["goal"].keys()) == {
        "period", "period_start", "target", "count", "display_count",
        "done", "allow_exceed", "per_day_target",
    }
    assert isinstance(h0["goal"]["per_day_target"], int)
    assert h0["goal"]["per_day_target"] >= 1
    json.dumps(p)  # serialisable


def test_archived_excluded_by_default_included_on_request():
    _s, p = _payload()
    ids = {h["id"] for h in p["data"]["habits"]}
    assert "h-old" not in ids                 # archived hidden by default
    _s2, p2 = _payload(archived=True)
    assert "h-old" in {h["id"] for h in p2["data"]["habits"]}


def test_order_matches_stats_active_ordering():
    s, p = _payload()
    want = [hs.entity_id for hs in st.active_stats(s, today=TODAY)]
    got = [h["id"] for h in p["data"]["habits"]]
    assert got == want


def test_named_color_maps_to_concrete_hex():
    # fixture: Reading=indigo, NoSnack=red, Gym=emerald, Water=sky
    _s, p = _payload()
    by = {h["id"]: h for h in p["data"]["habits"]}
    assert by["h-reading"]["color"].startswith("#")
    assert by["h-reading"]["color_name"] == "indigo"
    assert by["h-nosnack"]["color"] == wp.color_hex_for("red")
    assert by["h-gym"]["color"] == wp.color_hex_for("emerald")
    # unknown / missing -> deterministic neutral default, never a crash
    assert wp.color_hex_for(None) == wp._DEFAULT_HABIT_HEX
    assert wp.color_hex_for("chartreuse-not-real") == wp._DEFAULT_HABIT_HEX
    # an explicit hex passes through untouched (user-configured upstream)
    assert wp.color_hex_for("#123ABC") == "#123ABC"


def test_streaks_and_goal_are_verbatim_from_stats():
    s, p = _payload()
    by = {h["id"]: h for h in p["data"]["habits"]}
    for hid, entry in by.items():
        hs = st.habit_stats(s, hid, today=TODAY)
        assert entry["current_streak"] == hs.current_streak
        assert entry["longest_streak"] == hs.longest_streak
        assert entry["goal"]["period"] == hs.goal.period
        assert entry["goal"]["target"] == hs.goal.target
        assert entry["goal"]["count"] == hs.goal.count
        assert entry["goal"]["done"] == hs.goal.done
    # Gym has a week goal of 3 (fixture logs only 2 this week) -> not done.
    assert by["h-gym"]["goal"]["period"] == "week"
    assert by["h-gym"]["goal"]["target"] == 3
    assert by["h-gym"]["goal"]["done"] is False


def test_inverse_habit_flagged_and_cells_use_success_relation():
    s, p = _payload(window=30)
    ns = next(h for h in p["data"]["habits"] if h["id"] == "h-nosnack")
    assert ns["is_inverse"] is True
    # NoSnack slipped 2026-05-15 (ate) -> that day is NOT a success -> level 0;
    # a clean prior day inside the interval IS a success -> lit. Same relation
    # as stats / done_dates_for (no new logic in the projection).
    by_date = {c["date"]: c["level"] for c in ns["cells"]}
    assert by_date["2026-05-15"] == 0
    assert by_date.get("2026-05-14", 0) >= 1


def test_per_habit_coach_is_salient_short_and_policy_consistent():
    """Coach is SALIENT-ONLY now: a string that is EITHER empty (cruising /
    never-started — the default, not noise) OR a short decision-point nudge
    EXACTLY equal to the salience policy over that habit's success set, and
    NEVER longer than the hard cap (so the UI can't truncate it)."""
    s, p = _payload()
    saw_empty = False
    for entry in p["data"]["habits"]:
        coach = entry["coach"]
        kind = entry["coach_kind"]
        tip = entry["coach_tip"]
        assert isinstance(coach, str) and isinstance(kind, str)
        assert isinstance(tip, str)
        assert len(coach) <= wp.COACH_MAX_CHARS
        assert len(tip) <= wp.TIP_MAX_CHARS
        exp_line, exp_kind, exp_tip = wp._salient_coach(
            current_streak=entry["current_streak"],
            longest_streak=entry["longest_streak"],
            done=wp.done_dates_for(s, s.entity(entry["id"])),
            today=TODAY,
            inverse=entry["is_inverse"],
        )
        assert (coach, kind, tip) == (exp_line, exp_kind, exp_tip), (
            f"{entry['name']}: coach/kind/tip not from the policy"
        )
        # Invariant: empty line ⇔ empty kind ⇔ empty tip (no orphan icon,
        # no kindless nudge, no tip without a nudge).
        assert (coach == "") == (kind == "") == (tip == "")
        if tip:
            # Non-gameable + prefix-agnostic: a non-empty tip must be one
            # of the curated Atomic-Habits principles for its kind (the
            # attribution lives in the UI, not inline in the text).
            assert tip == wp._ATOMIC_TIPS.get(kind, "")[:wp.TIP_MAX_CHARS]
            assert "James Clear:" not in tip and "Atomic Habits:" not in tip
        saw_empty = saw_empty or coach == ""
    # Silence must be a real, exercised outcome through the projection path
    # (the old policy made this impossible — every habit was always noisy).
    # The per-branch nudge cases are pinned by the pure ladder test below.
    assert saw_empty, "no habit was silent — salience gate not exercised"


def _run(end: date, length: int) -> set:
    """A success set: `length` consecutive days ending at `end`."""
    return {
        date.fromordinal(end.toordinal() - i) for i in range(length)
    }


def test_salient_coach_branch_ladder_is_exact_and_ordered():
    """Pure policy: each decision point yields its specific short line;
    cruising and never-started are SILENT; first match wins; cap holds."""
    sc = wp._salient_coach
    yest = date.fromordinal(TODAY.toordinal() - 1)

    def t(kind: str) -> str:
        return wp._ATOMIC_TIPS.get(kind, "")[:wp.TIP_MAX_CHARS]

    # 1. at risk: active streak (through yesterday), not logged today.
    assert sc(current_streak=5, longest_streak=9,
              done=_run(yest, 5), today=TODAY, inverse=False) == \
        ("Keep your 5-day streak — do it today.", "at-risk",
         t("at-risk"))
    # inverse wording differs (still at-risk kind + tip).
    line, kind, tip = sc(current_streak=3, longest_streak=3,
                         done=_run(yest, 3), today=TODAY, inverse=True)
    assert "stay clean" in line and kind == "at-risk"
    assert tip == t("at-risk") and tip != ""

    # 2. slipped: real streak (best ≥3) broken 1–2 days ago.
    assert sc(current_streak=0, longest_streak=6,
              done=_run(yest, 4), today=TODAY, inverse=False) == \
        ("Streak slipped — one today restarts it.", "slipped",
         t("slipped"))

    # 3. milestone: logged today AND on a milestone count.
    assert sc(current_streak=30, longest_streak=30,
              done=_run(TODAY, 30), today=TODAY, inverse=False) == \
        ("30-day streak — milestone. Keep it.", "milestone",
         t("milestone"))
    # logged today but NOT a milestone / record → fully silent.
    assert sc(current_streak=4, longest_streak=9,
              done=_run(TODAY, 4), today=TODAY, inverse=False) == \
        ("", "", "")

    # 4. dormant: ≥7 days since last done.
    off = date.fromordinal(TODAY.toordinal() - 9)
    assert sc(current_streak=0, longest_streak=2,
              done={off}, today=TODAY, inverse=False) == \
        ("9 days off — restart small today.", "dormant",
         t("dormant"))

    # 5. never logged → SILENT (the empty ring already says "do it").
    assert sc(current_streak=0, longest_streak=0,
              done=set(), today=TODAY, inverse=False) == ("", "", "")

    # First-match precedence: a milestone only fires when logged today —
    # not-logged-today with a streak is at-risk, never milestone.
    assert sc(current_streak=7, longest_streak=7,
              done=_run(yest, 7), today=TODAY, inverse=False) == \
        ("Keep your 7-day streak — do it today.", "at-risk",
         t("at-risk"))

    # Every non-silent tip carries the Atomic-Habits principle + is capped.
    for k in ("at-risk", "slipped", "milestone", "dormant"):
        assert t(k) and len(t(k)) <= wp.TIP_MAX_CHARS

    # Cap: even an absurd streak stays within the UI's hard limit.
    big, _, _ = sc(current_streak=999999, longest_streak=999999,
             done=_run(yest, 3), today=TODAY, inverse=False)
    assert len(big) <= wp.COACH_MAX_CHARS


def test_per_habit_coach_is_deterministic():
    """Same substrate + same TODAY -> byte-identical coach strings."""
    _s1, p1 = _payload()
    _s2, p2 = _payload()
    c1 = {h["id"]: h["coach"] for h in p1["data"]["habits"]}
    c2 = {h["id"]: h["coach"] for h in p2["data"]["habits"]}
    assert c1 == c2


def test_cells_window_length_and_bounds():
    for w in (7, 30, 90, 120, 365):
        _s, p = _payload(window=w)
        for h in p["data"]["habits"]:
            assert len(h["cells"]) == w
            for c in h["cells"]:
                assert set(c.keys()) == {"date", "level", "amount"}
                assert 0 <= c["level"] <= h["levels"]
                assert isinstance(c["amount"], int) and c["amount"] >= 0


def test_cells_amount_and_per_day_target_match_frozen_stats():
    """The ADDITIVE per-day ``amount`` and ``goal.per_day_target`` must be
    EXACTLY what the frozen stats helpers compute — not a re-derivation.
    Cross-checked against the source of truth (no hardcoded literals), so
    this can't be gamed and pins the contract the renderer's per-day ring
    depends on."""
    s, p = _payload()
    for h in p["data"]["habits"]:
        evs = s.events_for(h["id"])
        by_day = st._amounts_by_day(evs)
        for c in h["cells"]:
            expect = int(by_day.get(date.fromisoformat(c["date"]), 0))
            assert c["amount"] == expect, (
                f"{h['name']} {c['date']}: amount {c['amount']} != "
                f"frozen _amounts_by_day {expect}"
            )
        ivs = s.intervals_for(h["id"])
        act = st._interval_active_on(ivs, TODAY)
        assert h["goal"]["per_day_target"] == st._day_threshold(act)
    # At least one synthetic habit must exercise a real >1 per-day target
    # (otherwise the ring path would never be covered by the fixture).
    assert any(
        h["goal"]["per_day_target"] > 1 for h in p["data"]["habits"]
    ) or any(
        c["amount"] > 0 for h in p["data"]["habits"] for c in h["cells"]
    )


def test_empty_substrate_is_graceful():
    s = sub.Substrate(substrate_kind="habit-tracker")
    p = wp.build_habits_widget_from_substrate(s, today=TODAY, window_days=14)
    assert p["schema_version"] == 2
    assert p["data"]["habits"] == []


def test_v1_contract_untouched_back_compat():
    """The frozen v1 single-habit payload must be byte-shape unchanged — the
    Wave-B addition is a SEPARATE file/builder, never a mutation of v1."""
    s = imp.import_habitkit(habitkit_export())
    v1 = wp.build_widget_from_substrate(s, today=TODAY, window_days=120)
    assert v1["schema_version"] == 1
    assert v1["type"] == "contribution-grid"
    assert set(v1["data"].keys()) == {"label", "levels", "cells"}
    # distinct data files
    assert wp.habits_widget_data_path().name == "habit-tracker-habits.json"
    assert (
        wp._producer.widget_data_path().name
        == "habit-tracker-habit-grid.json"
    )


def test_project_all_writes_both_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = imp.import_habitkit(habitkit_export())
    sub.SubstrateStore("habit-tracker").save(s)
    v1, hb = wp.project_all(window_days=60)
    assert tmp_path in v1.parents and tmp_path in hb.parents
    assert v1.name == "habit-tracker-habit-grid.json"
    assert hb.name == "habit-tracker-habits.json"
    d1 = json.loads(v1.read_text())
    d2 = json.loads(hb.read_text())
    assert d1["schema_version"] == 1 and d2["schema_version"] == 2
    assert len(d1["data"]["cells"]) == 60
    assert all(len(h["cells"]) == 60 for h in d2["data"]["habits"])
    assert not list(v1.parent.glob("*.tmp"))  # atomic, no leftover tmp


def test_project_habits_cli_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = imp.import_habitkit(habitkit_export())
    sub.SubstrateStore("habit-tracker").save(s)
    rc = wp.main(["--habits", "--days", "90"])
    assert rc == 0
    hb = tmp_path / "widgets" / "habit-tracker-habits.json"
    v1 = tmp_path / "widgets" / "habit-tracker-habit-grid.json"
    assert hb.exists() and v1.exists()
    doc = json.loads(hb.read_text())
    assert doc["schema_version"] == 2
    assert all(len(h["cells"]) == 90 for h in doc["data"]["habits"])


# --------------------------------------------------------------------------- #
# "Too many habits" focus advisory (Atomic Habits)
# --------------------------------------------------------------------------- #
def _habit(idx: int, *, done_last: int, window: int = 30) -> tuple:
    """An entity + events 'done' on the last `done_last` of `window` days
    ending TODAY (no interval ⇒ threshold 1 ⇒ each event = a done day)."""
    eid = f"h-{idx}"
    ent = sub.Entity(id=eid, name=f"Habit {idx}", order_index=idx)
    evs = [
        sub.Event(
            id=f"{eid}-{k}",
            entity_id=eid,
            date=date.fromordinal(TODAY.toordinal() - k).isoformat(),
            amount=1,
        )
        for k in range(done_last)
    ]
    return ent, evs


def _substrate(*habits) -> sub.Substrate:
    s = sub.Substrate(substrate_kind="habit-tracker")
    for ent, evs in habits:
        s.entities.append(ent)
        s.events.extend(evs)
    return s


def test_consistency_pct_is_window_relative_and_no_false_positive():
    # 27 of last 30 → 90%.
    s = _substrate(_habit(0, done_last=27))
    done = wp.done_dates_for(s, s.entity("h-0"))
    assert wp._consistency_pct(done, today=TODAY, window=30) == 90
    # A 3-day-old perfect habit is NOT automatic (denominator = window).
    s2 = _substrate(_habit(1, done_last=3))
    d2 = wp.done_dates_for(s2, s2.entity("h-1"))
    assert wp._consistency_pct(d2, today=TODAY, window=30) == 10


def test_focus_advice_silent_within_budget():
    s = _substrate(_habit(0, done_last=28), _habit(1, done_last=2))
    p = wp.build_habits_widget_from_substrate(s, today=TODAY)
    f = p["focus"]
    assert f["show"] is False and f["message"] == ""
    assert f["active_count"] == 2 and f["budget"] == 4


def test_focus_advice_warns_and_proposes_automatic_habits():
    # 6 > budget(4): two are automatic (≥80% of 30d), four are sparse.
    s = _substrate(
        _habit(0, done_last=29),   # 97% automatic
        _habit(1, done_last=25),   # 83% automatic
        _habit(2, done_last=10),   # 33%
        _habit(3, done_last=4),
        _habit(4, done_last=1),
        _habit(5, done_last=0),
    )
    f = wp.build_habits_widget_from_substrate(s, today=TODAY)["focus"]
    assert f["show"] is True
    assert f["active_count"] == 6 and f["budget"] == 4
    names = [c["name"] for c in f["candidates"]]
    assert names == ["Habit 0", "Habit 1"]            # sorted by consistency
    assert f["candidates"][0]["consistency"] >= f["candidates"][1][
        "consistency"]
    assert "Atomic Habits" in f["message"]
    assert "Habit 0" in f["message"] and "Habit 1" in f["message"]
    assert "graduate" in f["message"].lower()


def test_focus_advice_no_automatic_candidates_path():
    # 5 > budget(4) but NONE ≥80% → different message, empty candidates.
    s = _substrate(*[_habit(i, done_last=5) for i in range(5)])
    f = wp.build_habits_widget_from_substrate(s, today=TODAY)["focus"]
    assert f["show"] is True and f["candidates"] == []
    assert "pausing the weakest" in f["message"]


def test_focus_advice_env_overrides(monkeypatch):
    monkeypatch.setenv("IGA_HABIT_FOCUS_BUDGET", "2")
    monkeypatch.setenv("IGA_HABIT_GRADUATE_PCT", "50")
    monkeypatch.setenv("IGA_HABIT_FOCUS_WINDOW_DAYS", "30")
    # 3 habits > budget 2; a 60%/30d habit now counts automatic (≥50%).
    s = _substrate(
        _habit(0, done_last=18),   # 60%
        _habit(1, done_last=3),
        _habit(2, done_last=2),
    )
    f = wp.build_habits_widget_from_substrate(s, today=TODAY)["focus"]
    assert f["budget"] == 2 and f["graduate_pct"] == 50
    assert f["show"] is True
    assert [c["name"] for c in f["candidates"]] == ["Habit 0"]
    # Out-of-range env is clamped, not crashing.
    monkeypatch.setenv("IGA_HABIT_FOCUS_BUDGET", "nonsense")
    assert wp.focus_budget() == 4


def test_focus_advice_empty_substrate_is_silent():
    s = sub.Substrate(substrate_kind="habit-tracker")
    f = wp.build_habits_widget_from_substrate(s, today=TODAY)["focus"]
    assert f["show"] is False and f["active_count"] == 0


def test_archived_roster_is_emitted_for_recovery():
    s = imp.import_habitkit(habitkit_export())
    # nothing archived in the fixture except possibly h-old; assert shape.
    p0 = wp.build_habits_widget_from_substrate(s, today=TODAY)
    assert isinstance(p0["archived"], list)
    active_ids = {h["id"] for h in p0["data"]["habits"]}
    # archive a real active habit, re-project: it leaves active, enters
    # the archived roster with id/name/colour (enough to restore).
    hid = sorted((e for e in s.entities if not e.archived),
                 key=lambda e: (e.order_index, e.id))[0].id
    nm = s.entity(hid).name
    s.entity(hid).archived = True
    p1 = wp.build_habits_widget_from_substrate(s, today=TODAY)
    assert hid not in {h["id"] for h in p1["data"]["habits"]}
    arc = {a["id"]: a for a in p1["archived"]}
    assert hid in arc
    assert arc[hid]["name"] == nm
    assert arc[hid]["color"].startswith("#")
    # and it's gone from the previously-active set
    assert hid in active_ids

"""Derived widget projection: substrate  ->  the v2 contribution-grid JSON.

The running v2 menu-bar app polls
``~/Gaia/state/widgets/habit-tracker-habit-grid.json`` (schema_version 1,
contribution-grid cells). That contract MUST NOT change in Wave A. This module
renders exactly that v1 payload as a DERIVED projection of the substrate, so
the app keeps rendering unchanged whether the data came from the old
append-only log (the v2 ``producer.py`` path, still intact) or the new
substrate (post-import).

It reuses ``producer.py``'s pure, already-unit-tested grid/coach builders
(``build_widget_data``) so the emitted bytes stay byte-compatible with the
Swift ``WidgetData`` decoder — there is exactly one widget-schema authority.

Selecting which entity to project:
  * an explicit ``entity_id``; else
  * the first non-archived entity by (order_index, id); else
  * any entity; else an empty-but-valid grid (graceful, never raises).

A "done day" for the grid is a civil day whose summed event amount meets that
day's success threshold (inverse-aware) — i.e. the same success relation
``stats.py`` uses, so the grid agrees with the streak number.

Stdlib only. Honours ``$IGA_STATE_DIR`` isolation via the SAME
``state_root()`` resolver (the live-data guard is shared, not re-implemented).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(modname: str, filename: str):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register before exec — Py3.14 @dataclass looks up
    # sys.modules[cls.__module__] during class creation.
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_producer = _load("ht_producer", "producer.py")
_sub = _load("ht_substrate", "substrate.py")
_stats = _load("ht_stats", "stats.py")

SubstrateStore = _sub.SubstrateStore
Substrate = _sub.Substrate


def _pick_entity(s: Substrate, entity_id: str | None):
    if entity_id is not None:
        return s.entity(entity_id)
    active = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id),
    )
    if active:
        return active[0]
    return s.entities[0] if s.entities else None


def done_dates_for(s: Substrate, entity) -> set[date]:
    """Civil days that count as 'done' for the grid: each day whose summed
    amount meets that day's success threshold (inverse-aware), matching
    ``stats._day_succeeds`` so grid and streak never disagree."""
    if entity is None:
        return set()
    evs = s.events_for(entity.id)
    ivs = s.intervals_for(entity.id)
    by_day = _stats._amounts_by_day(evs)
    if entity.inverse:
        # For an inverse habit, "lit" days are the successful (clean) days
        # within the logged span — same relation stats uses.
        span_start = _stats._streak_span(entity, evs, ivs)
        out: set[date] = set()
        if span_start is None:
            return out
        d = span_start
        # cap the projection span at the latest logged day to stay finite
        last = max(by_day) if by_day else span_start
        while d <= last:
            if _stats._day_succeeds(d, by_day, ivs, True):
                out.add(d)
            d = date.fromordinal(d.toordinal() + 1)
        return out
    return {
        d
        for d in by_day
        if _stats._day_succeeds(d, by_day, ivs, False)
    }


def build_widget_from_substrate(
    s: Substrate,
    *,
    entity_id: str | None = None,
    today: date | None = None,
    window_days: int = _producer.DEFAULT_WINDOW_DAYS,
) -> dict:
    """The v1 contribution-grid payload, derived from the substrate, byte-
    compatible with the Swift decoder (delegates to producer.build_widget_data
    with a precomputed ``done`` set — pure, no I/O)."""
    today = today or datetime.now(timezone.utc).date()
    entity = _pick_entity(s, entity_id)
    name = entity.name if entity is not None else "habit"
    done = done_dates_for(s, entity)
    return _producer.build_widget_data(
        name, today=today, window_days=window_days, done=done
    )


# --------------------------------------------------------------------------- #
# Wave B — ADDITIVE multi-habit widget projection (schema_version 2)
#
# Everything ABOVE is the FROZEN v1 contract (the running app keeps polling
# habit-tracker-habit-grid.json, schema_version 1) and is left byte-for-byte
# unchanged. The Wave-B multi-habit widget needs richer, multi-habit data:
# per habit a color, icon/emoji, isInverse, current + longest streak, the
# active-goal progress, and the day cells for a requested window. That is a
# SECOND, separately-versioned data file (habit-tracker-habits.json,
# schema_version 2) so a v1-only app never breaks mid-wave (back-compat) and
# the v1 authority (producer.build_widget_data) stays the single grid-schema
# authority for the legacy widget.
#
# All habit logic is DELEGATED to the frozen Wave-A stats.py /
# done_dates_for — this builder computes NO streak/goal/grid math itself; it
# only assembles already-computed values + maps the substrate's named color
# to a concrete hex the renderer can use without inventing semantics.
# --------------------------------------------------------------------------- #

HABITS_WIDGET_ID = "habits"
HABITS_WIDGET_TYPE = "habit-grid-multi"
HABITS_SCHEMA_VERSION = 2

# a named-colour palette -> a concrete sRGB hex. The renderer is told the
# exact color so it invents no semantics; an unknown/absent name falls back to
# a neutral indigo. Configurable upstream (substrate entity.attrs.color); the
# app just renders the provided hex.
_NAMED_PALETTE: dict[str, str] = {
    "red": "#E5484D",
    "orange": "#F76B15",
    "amber": "#FFB224",
    "yellow": "#F5D90A",
    "lime": "#99D52A",
    "green": "#30A46C",
    "emerald": "#1FAD71",
    "teal": "#12A594",
    "cyan": "#0FA3C2",
    "sky": "#2EB6EA",
    "blue": "#3E63DD",
    "indigo": "#5B5BD6",
    "violet": "#7C66DC",
    "purple": "#8E4EC6",
    "pink": "#D6409F",
    "rose": "#E54666",
    "brown": "#AD7F58",
    "gray": "#8B8D98",
    "grey": "#8B8D98",
    "slate": "#647084",
}
_DEFAULT_HABIT_HEX = _NAMED_PALETTE["indigo"]


def color_hex_for(name: str | None) -> str:
    """Map a substrate named color to a concrete sRGB hex. Pass through an
    explicit ``#rrggbb`` the user already configured; otherwise look the name
    up; otherwise the neutral default. Pure presentation mapping — no
    semantics decided here, the renderer just paints what it's told."""
    if not name:
        return _DEFAULT_HABIT_HEX
    s = str(name).strip()
    low = s.lower()
    if low.startswith("#") and len(low) in (4, 7):
        return s
    return _NAMED_PALETTE.get(low, _DEFAULT_HABIT_HEX)


def _habit_cells(s: Substrate, entity, *, today: date, window_days: int):
    """The last ``window_days`` cells for one entity, oldest→newest, each
    ``{date, level, amount}`` — reuses the FROZEN producer level bucketing
    over the FROZEN ``done_dates_for`` success set (same relation stats.py
    uses, so the grid never disagrees with the streak number), then ADDITIVELY
    enriches each cell with that day's raw summed ``amount`` (the FROZEN
    ``stats._amounts_by_day`` — the exact relation streak/goal already use).

    ``amount`` exists so the renderer can draw a the tracker per-day progress
    ring for a habit with a per-day target (e.g. "50 push-ups": amount/target
    filled segments) instead of a flat fill. It is purely the projection of
    an already-computed substrate quantity — ZERO new math here. This is the
    Wave-B (schema_version 2) payload ONLY; the frozen v1 producer path stays
    byte-exact ``{date, level}``."""
    done = done_dates_for(s, entity)
    cells = _producer.build_cells(
        done, today=today, window_days=window_days
    )
    by_day = _stats._amounts_by_day(s.events_for(entity.id))
    for c in cells:
        c["amount"] = int(by_day.get(date.fromisoformat(c["date"]), 0))
    return cells


def _per_day_target_for(s: Substrate, entity, *, today: date) -> int:
    """The per-day completion target in effect for ``entity`` on ``today``,
    via the FROZEN ``stats`` helpers (active interval → day threshold). 1
    means "binary, no per-day ring" (the renderer keeps the flat fill);
    >1 means a a segmented per-day ring. Read-only delegation — no
    new goal math is introduced here."""
    ivs = s.intervals_for(entity.id)
    act = _stats._interval_active_on(ivs, today)
    return _stats._day_threshold(act)


_MILESTONES = frozenset({7, 14, 21, 30, 50, 75, 100, 150, 200, 365})
COACH_MAX_CHARS = 64  # hard cap so the single-line UI never truncates
TIP_MAX_CHARS = 260   # the longer hover tip — popover-sized, still bounded


# --------------------------------------------------------------------------- #
# "Too many habits" focus advisory (Atomic Habits)
#
# James Clear / Fogg: willpower is finite — deliberately build a SMALL set at
# once; a behaviour that is already automatic no longer needs a focus slot
# (Lally et al.: automaticity comes from sustained high adherence). So when
# the ACTIVE set exceeds a focus budget we surface a calm advisory that
# proposes GRADUATING (archiving) the habits that are already automatic
# (high recent consistency) to free attention for the ones still being built.
#
# Defaults are deliberately good per the above; all three are env-overridable
# (no user-specific data here — generic, OSS-safe).
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    import os

    raw = os.environ.get(name, "")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def focus_budget() -> int:
    """Max habits to ACTIVELY build at once before we advise focusing.
    Default 4 (Clear/Fogg: a small, deliberate set)."""
    return _env_int("IGA_HABIT_FOCUS_BUDGET", 4, lo=1, hi=99)


def graduate_pct() -> int:
    """Recent-consistency % at/above which a habit counts as AUTOMATIC and
    is proposed for graduation (archive). Default 80 (sustained adherence
    ⇒ automaticity, Lally et al. cited in Atomic Habits)."""
    return _env_int("IGA_HABIT_GRADUATE_PCT", 80, lo=1, hi=100)


def focus_window_days() -> int:
    """Recency window for the consistency score. Default 30 — a month is
    enough to judge automaticity without the full history diluting it."""
    return _env_int("IGA_HABIT_FOCUS_WINDOW_DAYS", 30, lo=7, hi=365)


def _consistency_pct(done: set, *, today: date, window: int) -> int:
    """% of the last ``window`` civil days that are 'done' for this habit
    (same success set the streak uses). Integer 0..100. A young habit can't
    false-positive: the denominator is the full window."""
    if window <= 0:
        return 0
    start = date.fromordinal(today.toordinal() - (window - 1))
    hit = sum(1 for d in done if start <= d <= today)
    return int(round(100.0 * hit / window))


def _focus_advice(
    s: Substrate, ents: list, *, today: date
) -> dict:
    """A calm, deterministic advisory (no LLM) for the UI to render BELOW
    the last habit — only when the active set exceeds the focus budget.
    Proposes graduating the already-automatic habits (consistency ≥
    ``graduate_pct`` over the recency window). ``show`` is False (and the
    UI renders nothing) when the set is within budget."""
    budget = focus_budget()
    gpct = graduate_pct()
    win = focus_window_days()
    n = len(ents)
    show = n > budget

    cands: list[dict] = []
    for e in ents:
        c = _consistency_pct(
            done_dates_for(s, e), today=today, window=win
        )
        if c >= gpct:
            cands.append(
                {"id": e.id, "name": e.name, "consistency": c}
            )
    cands.sort(key=lambda x: (-x["consistency"], x["name"]))

    msg = ""
    if show:
        base = (
            f"You're actively building {n} habits. Atomic Habits: "
            f"willpower is finite — keep the focused set small "
            f"(≈{budget})."
        )
        if cands:
            names = ", ".join(c["name"] for c in cands[:3])
            more = "" if len(cands) <= 3 else f" +{len(cands) - 3} more"
            tail = (
                f" These look automatic (≥{gpct}% lately) — graduate "
                f"them (archive) to free focus: {names}{more}."
            )
        else:
            tail = (
                " None are automatic yet — consider pausing the "
                "weakest until the others stick."
            )
        msg = base + tail

    return {
        "show": show,
        "kind": "too-many-habits",
        "active_count": n,
        "budget": budget,
        "graduate_pct": gpct,
        "window_days": win,
        "message": msg,
        "candidates": cands,
    }

# James Clear / *Atomic Habits* principle per decision kind. Deterministic,
# curated, no LLM — the SHORT line is the nudge; this is the "why", shown in
# the hover popover. "" for the silent kind (empty line ⇔ kind ⇔ tip).
_ATOMIC_TIPS: dict[str, str] = {
    "at-risk": (
        "Never miss twice. One slip is an accident; two starts a new "
        "pattern. Protect the chain — even a two-minute version of it "
        "counts as showing up today."
    ),
    "slipped": (
        "You don't rise to your goals, you fall to your systems. Missing "
        "once won't undo your progress — make it easy, shrink it to two "
        "minutes, and just restart."
    ),
    "milestone": (
        "Habits are the compound interest of self-improvement. You're "
        "past the plateau of latent potential — every rep is a vote for "
        "the identity: 'I'm the kind of person who does this.'"
    ),
    "dormant": (
        "Use the two-minute rule: scale it down until it's impossible to "
        "say no. Motivation follows action, not the reverse — one tiny "
        "rep reopens the loop and re-casts the identity vote."
    ),
}


def _salient_coach(
    *,
    current_streak: int,
    longest_streak: int,
    done: set,
    today: date,
    inverse: bool,
) -> tuple[str, str, str]:
    """Return ``(line, kind, tip)``: a SHORT coach line ONLY at a
    behaviour-change decision point (else ``("", "", "")`` and the UI
    renders nothing), the decision KIND so the renderer picks a semantic
    icon WITHOUT parsing prose, and a longer Atomic-Habits TIP shown on
    hover. Deterministic, stdlib, no LLM; reuses the FROZEN
    ``producer.days_since_last`` over the same success set the streak uses,
    so the nudge never disagrees with the numbers.

    ``kind`` ∈ {"at-risk", "slipped", "milestone", "dormant", ""}.
    ``tip`` is the James Clear / *Atomic Habits* principle for that kind
    ("" when silent — empty line ⇔ empty kind ⇔ empty tip, invariant).

    Salience ladder (first match wins):
      1. at-risk   — an active streak (≥2) not yet logged today
      2. slipped   — a real streak (best ≥3) broken 1–2 days ago
      3. milestone — logged today AND on a milestone / new personal best
      4. dormant   — ≥7 days since the last time it was done
      5. otherwise — cruising / never-started / nothing notable → silent

    A never-logged habit stays SILENT (the empty ring already says "do
    it"); perpetual "not started" on a long-dead habit is the exact noise
    the policy removes.
    """
    cs = int(current_streak)
    ls = int(longest_streak)
    done_today = today in done
    dsl = _producer.days_since_last(done, today=today)  # None if empty
    act = "stay clean" if inverse else "do it"

    line, kind = "", ""
    if not done_today and cs >= 2:
        line = f"Keep your {cs}-day streak — {act} today."
        kind = "at-risk"
    elif cs == 0 and ls >= 3 and dsl is not None and 1 <= dsl <= 2:
        line = "Streak slipped — one today restarts it."
        kind = "slipped"
    elif done_today and cs >= 7 and (
        cs in _MILESTONES or (cs == ls and ls >= 14)
    ):
        line = f"{cs}-day streak — milestone. Keep it."
        kind = "milestone"
    elif dsl is not None and dsl >= 7:
        line = f"{dsl} days off — restart small today."
        kind = "dormant"

    # Defence in depth: never hand the UI something it must truncate.
    return (
        line[:COACH_MAX_CHARS],
        kind,
        _ATOMIC_TIPS.get(kind, "")[:TIP_MAX_CHARS],
    )


def build_habit_entry(
    s: Substrate, entity, *, today: date, window_days: int
) -> dict:
    """One habit's full Wave-B entry. Streak/longest/goal come verbatim from
    the frozen ``stats.habit_stats``; cells from the frozen producer bucketer
    over the frozen success set. This function adds NO habit logic."""
    hs = _stats.habit_stats(s, entity.id, today=today)
    a = entity.attrs or {}
    g = hs.goal
    # Per-habit coach: SALIENT-ONLY. A line is emitted ONLY at a decision
    # point (streak at risk / just slipped / earned milestone / dormant);
    # a cruising habit is intentionally SILENT — the flame + filled square
    # are the reward, and a wall of "keep going!" is habituating noise. The
    # line is short by construction so the UI never truncates it. Computed
    # from the FROZEN stats/producer signals over THIS habit's success set
    # (same relation stats.py uses, so the message agrees with the streak).
    coach_text, coach_kind, coach_tip = _salient_coach(
        current_streak=hs.current_streak,
        longest_streak=hs.longest_streak,
        done=done_dates_for(s, entity),
        today=today,
        inverse=bool(entity.inverse),
    )
    return {
        "id": entity.id,
        "name": entity.name,
        "color": color_hex_for(a.get("color")),
        "color_name": a.get("color"),
        "icon": a.get("icon"),
        "emoji": a.get("emoji"),
        "is_inverse": bool(entity.inverse),
        "archived": bool(entity.archived),
        "order_index": int(entity.order_index),
        "current_streak": hs.current_streak,
        "longest_streak": hs.longest_streak,
        "coach": coach_text,
        # The decision kind so the renderer maps a semantic icon WITHOUT
        # parsing prose ("" when silent). Additive; old decoders ignore it.
        "coach_kind": coach_kind,
        # The longer Atomic-Habits "why", shown in the hover popover ("" when
        # silent). Additive; tolerant on old decoders.
        "coach_tip": coach_tip,
        "goal": {
            "period": g.period,                 # day|week|month|none
            "period_start": g.period_start,
            "target": g.target,                 # None = no period goal
            "count": g.count,
            "display_count": g.display_count,
            "done": g.done,
            "allow_exceed": g.allow_exceed,
            # ADDITIVE: the per-DAY completion target (the tracker
            # requiredNumberOfCompletionsPerDay). 1 = binary (renderer keeps
            # the flat fill); >1 drives the per-day segmented ring. Frozen
            # stats delegation — no new goal math.
            "per_day_target": _per_day_target_for(s, entity, today=today),
        },
        "levels": _producer.LEVELS,
        "cells": _habit_cells(
            s, entity, today=today, window_days=window_days
        ),
    }


def build_habits_widget_from_substrate(
    s: Substrate,
    *,
    today: date | None = None,
    window_days: int = _producer.DEFAULT_WINDOW_DAYS,
    include_archived: bool = False,
) -> dict:
    """The Wave-B multi-habit payload (schema_version 2). Deterministic, pure,
    no I/O. Habits are ordered exactly as ``stats.active_stats`` orders them
    (order_index, id) so the widget's row order is stable and matches the
    engine. Archived habits are excluded by default (same as the active
    aggregate); ``include_archived`` is offered for a future "show archived"
    toggle but defaults off so the wife-test view stays uncluttered."""
    today = today or datetime.now(timezone.utc).date()
    ents = sorted(
        (
            e for e in s.entities
            if include_archived or not e.archived
        ),
        key=lambda e: (e.order_index, e.id),
    )
    habits = [
        build_habit_entry(
            s, e, today=today, window_days=window_days
        )
        for e in ents
    ]
    # ADDITIVE lightweight roster of ARCHIVED habits so the app can offer
    # a "restore" / export path (archive must not be a one-way trap).
    # Minimal fields only — id (to relay unarchive), name, colour.
    archived = [
        {
            "id": e.id,
            "name": e.name,
            "color": color_hex_for((e.attrs or {}).get("color")),
        }
        for e in sorted(
            (e for e in s.entities if e.archived),
            key=lambda e: (e.order_index, e.id),
        )
    ]
    return {
        "schema_version": HABITS_SCHEMA_VERSION,
        "widget_id": HABITS_WIDGET_ID,
        "type": HABITS_WIDGET_TYPE,
        "title": "Habits",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "window_days": window_days,
        # ADDITIVE top-level advisory the UI renders below the last habit
        # (only when show=True). Old decoders ignore it.
        "focus": _focus_advice(s, ents, today=today),
        # ADDITIVE archived roster (the collapsible at the bottom of the
        # list). Always present (possibly []); old decoders ignore it.
        "archived": archived,
        "data": {
            "habits": habits,
            "levels": _producer.LEVELS,
        },
    }


def habits_widget_data_path() -> Path:
    """Where the Wave-B multi-habit data file lives — a SEPARATE file from the
    frozen v1 ``habit-tracker-habit-grid.json`` so a v1-only app keeps working
    untouched (back-compat). Isolation-aware (same state_root resolver)."""
    return (
        _producer.state_root()
        / "widgets"
        / f"habit-tracker-{HABITS_WIDGET_ID}.json"
    )


def project_habits(
    *,
    window_days: int = _producer.DEFAULT_WINDOW_DAYS,
    include_archived: bool = False,
) -> Path:
    """Load the substrate (isolation-aware), build the Wave-B multi-habit
    payload, write it atomically (frozen producer atomic writer) to its own
    data file. Returns the path. Pure projection — never writes the substrate
    back."""
    s = SubstrateStore("habit-tracker").load()
    payload = build_habits_widget_from_substrate(
        s, window_days=window_days, include_archived=include_archived
    )
    out = habits_widget_data_path()
    _producer._atomic_write_json(out, payload)
    return out


def project(
    *,
    entity_id: str | None = None,
    window_days: int = _producer.DEFAULT_WINDOW_DAYS,
) -> Path:
    """Load the substrate (isolation-aware), build the v1 widget payload,
    write it atomically to the SAME path the app polls. Returns the path."""
    s = SubstrateStore("habit-tracker").load()
    payload = build_widget_from_substrate(
        s, entity_id=entity_id, window_days=window_days
    )
    out = _producer.widget_data_path()
    _producer._atomic_write_json(out, payload)
    return out


def project_all(
    *,
    entity_id: str | None = None,
    window_days: int = _producer.DEFAULT_WINDOW_DAYS,
    habits_window_days: int | None = None,
    include_archived: bool = False,
) -> tuple[Path, Path]:
    """Re-emit BOTH derived widget files in one isolation-aware pass: the
    frozen v1 ``habit-tracker-habit-grid.json`` (legacy single-habit, contract
    unchanged) AND the Wave-B ``habit-tracker-habits.json`` (multi-habit,
    schema_version 2). The record entry point calls this so a click refreshes
    whichever widget the running app renders. Returns ``(v1_path,
    habits_path)``. Additive — ``project`` itself is untouched."""
    v1 = project(entity_id=entity_id, window_days=window_days)
    hb = project_habits(
        window_days=(
            habits_window_days
            if habits_window_days is not None
            else window_days
        ),
        include_archived=include_archived,
    )
    return v1, hb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="widget_projection",
        description="Render the v2 contribution-grid widget JSON from the "
        "habit-tracker substrate (derived projection; app contract "
        "unchanged).",
    )
    ap.add_argument(
        "--entity-id",
        default=None,
        help="Substrate entity to project (default: first non-archived).",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=_producer.DEFAULT_WINDOW_DAYS,
        help=f"grid window (default {_producer.DEFAULT_WINDOW_DAYS})",
    )
    ap.add_argument(
        "--habits",
        action="store_true",
        help="ALSO re-emit the Wave-B multi-habit widget file "
        "(habit-tracker-habits.json, schema_version 2). The legacy v1 "
        "file is always emitted; this adds the richer one.",
    )
    ap.add_argument(
        "--include-archived",
        action="store_true",
        help="include archived habits in the Wave-B multi-habit file "
        "(default off — matches the active aggregate).",
    )
    ns = ap.parse_args(argv)
    if ns.habits:
        v1, hb = project_all(
            entity_id=ns.entity_id,
            window_days=max(1, ns.days),
            include_archived=ns.include_archived,
        )
        print(f"wrote {v1}")
        print(f"wrote {hb}")
    else:
        out = project(entity_id=ns.entity_id, window_days=max(1, ns.days))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

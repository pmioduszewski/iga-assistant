"""Habit-tracker widget producer — stdlib only, no LLM, fully deterministic.

WHY THIS EXISTS
---------------
This is the data-file producer half of the v2 widget contract:

    A widget = a declarative spec (the ``widgets:`` block in SKILL.md) + a
    data file. The skill produces the data file; the app renders ONLY known
    widget types from it. The app holds zero habit logic. Deleting the app
    changes nothing here — this module still emits a valid widget JSON.

It reads a plain append-only habits log and emits the v1 widget data-file
JSON (schema below) atomically, so a polling reader never sees a half-written
file.

INPUT  : ~/Iga/state/habits/<name>.log   (one ISO date per line, dups OK)
OUTPUT : ~/Iga/state/widgets/habit-tracker-habit-grid.json

STATE-ROOT OVERRIDE (test / sandbox isolation — DATA-LOSS GUARD)
---------------------------------------------------------------
By default the producer reads + writes under the real ``~/Iga/state``
tree, so the live widget keeps working. Tests, the app deletion-invariant
test, and any sandboxed run MUST NOT clobber the user's live data. Set
``$IGA_STATE_DIR`` to redirect the ENTIRE state tree (both the habit log
dir and the widget output) somewhere safe (e.g. a pytest ``tmp_path``):

    IGA_STATE_DIR=/some/tmp/state  →  log:    /some/tmp/state/habits/<name>.log
                                      widget: /some/tmp/state/widgets/...

Precedence: ``$IGA_STATE_DIR`` (explicit state root) > ``$IGA_HOME``/state
(repo-root override) > ``~/Iga/state`` (default — live data, unchanged).
When ``$IGA_STATE_DIR`` is set, NOTHING under the real ``~/Iga/state`` is
read or written.

WIDGET DATA-FILE SCHEMA (v1) — must stay byte-compatible with the Swift
``WidgetData`` decoder:

    {
      "schema_version": 1,
      "widget_id": "habit-grid",
      "type": "contribution-grid",
      "title": "Habit streak",
      "generated_at": "<ISO8601 UTC>",
      "data": {
        "label": "<habit> — <streak summary>",
        "levels": 4,
        "cells": [ {"date": "YYYY-MM-DD", "level": 0..4}, ... ]
      },
      "coach": {"text": "<deterministic sentence>", "tone": "encouraging"} | null
    }

The coach line is DETERMINISTIC — derived purely from streak / days-missed
arithmetic. No model call. (An LLM coach via an iga-proactive ``nudge`` job is
the documented next generalization; the data-file contract does not change.)

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
WIDGET_ID = "habit-grid"
WIDGET_TYPE = "contribution-grid"
WIDGET_TITLE = "Habit streak"
LEVELS = 4  # number of non-zero intensity buckets (grid has 0..LEVELS)

DEFAULT_WINDOW_DAYS = 120
# Trailing window used to bucket a day's intensity: how many of the last
# INTENSITY_WINDOW days (ending on that day) the habit was done.
INTENSITY_WINDOW = 7


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def _iga_root() -> Path:
    """Repo / state root. ``$IGA_HOME`` overrides; else ``~/Iga``."""
    env = os.environ.get("IGA_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Iga"


def state_root() -> Path:
    """Root of the state tree for ALL producer reads/writes.

    Precedence (data-loss guard — see module docstring):
      1. ``$IGA_STATE_DIR``  — explicit state-root override (tests/sandbox).
         Used verbatim; the ``state/`` segment is the override itself.
      2. ``$IGA_HOME``/state — repo-root override.
      3. ``~/Iga/state``    — default; the user's LIVE data, unchanged.

    When (1) is set, nothing under the real ``~/Iga/state`` is touched.
    """
    env = os.environ.get("IGA_STATE_DIR")
    if env:
        return Path(env).expanduser()
    return _iga_root() / "state"


def habits_log_path(name: str) -> Path:
    return state_root() / "habits" / f"{name}.log"


def widget_data_path() -> Path:
    return (
        state_root()
        / "widgets"
        / f"habit-tracker-{WIDGET_ID}.json"
    )


# --------------------------------------------------------------------------- #
# Log parsing
# --------------------------------------------------------------------------- #
def parse_log(text: str) -> set[date]:
    """Parse the append-only log into a set of done-dates.

    One ``YYYY-MM-DD`` per line. Blank lines, surrounding whitespace, and
    duplicates are tolerated. Unparseable lines are skipped silently (the log
    is user-appended; one bad line must not break the widget).
    """
    out: set[date] = set()
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        try:
            out.add(date.fromisoformat(s[:10]))
        except ValueError:
            continue
    return out


def read_done_dates(name: str) -> set[date]:
    p = habits_log_path(name)
    try:
        return parse_log(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except OSError:
        # Unreadable log → behave like empty (graceful, never raise).
        return set()


# --------------------------------------------------------------------------- #
# Grid math (pure, deterministic, unit-tested)
# --------------------------------------------------------------------------- #
def _level_for_day(day: date, done: set[date]) -> int:
    """Bucket a day's intensity 0..LEVELS.

    0  → habit not done that day.
    >0 → done that day; brighter the more of the trailing INTENSITY_WINDOW
         days (ending on `day`) were also done. This makes a sustained streak
         glow brighter than an isolated single day — the HabitKit feel.
    """
    if day not in done:
        return 0
    window_hits = sum(
        1
        for i in range(INTENSITY_WINDOW)
        if (day - timedelta(days=i)) in done
    )
    # window_hits is 1..INTENSITY_WINDOW (>=1 because `day` itself is in done).
    # Map onto 1..LEVELS.
    frac = window_hits / INTENSITY_WINDOW
    level = 1 + int(round(frac * (LEVELS - 1)))
    return max(1, min(LEVELS, level))


def build_cells(
    done: set[date], *, today: date, window_days: int
) -> list[dict]:
    """Last ``window_days`` cells, oldest→newest, each ``{date, level}``."""
    cells: list[dict] = []
    start = today - timedelta(days=window_days - 1)
    d = start
    while d <= today:
        cells.append(
            {"date": d.isoformat(), "level": _level_for_day(d, done)}
        )
        d += timedelta(days=1)
    return cells


def current_streak(done: set[date], *, today: date) -> int:
    """Consecutive done-days ending today OR yesterday.

    Counting from yesterday too means the streak isn't shown as "broken"
    just because today's entry hasn't been logged yet.
    """
    if not done:
        return 0
    anchor = today if today in done else (
        today - timedelta(days=1)
        if (today - timedelta(days=1)) in done
        else None
    )
    if anchor is None:
        return 0
    streak = 0
    d = anchor
    while d in done:
        streak += 1
        d -= timedelta(days=1)
    return streak


def days_since_last(done: set[date], *, today: date) -> int | None:
    """Whole days since the most recent done-day. None if log empty.

    0 = done today, 1 = done yesterday, ...
    """
    if not done:
        return None
    recent = max(d for d in done if d <= today) if any(
        d <= today for d in done
    ) else max(done)
    return (today - recent).days


def total_in_window(
    done: set[date], *, today: date, window_days: int
) -> int:
    start = today - timedelta(days=window_days - 1)
    return sum(1 for d in done if start <= d <= today)


# --------------------------------------------------------------------------- #
# Deterministic coach line (NO LLM)
# --------------------------------------------------------------------------- #
def coach_line(
    done: set[date], *, today: date, window_days: int
) -> dict | None:
    """A deterministic, data-derived coach sentence + tone.

    Pure arithmetic on streak / days-missed / window total — reproducible and
    unit-testable. Tone is one of: encouraging | nudge | neutral.
    """
    if not done:
        return {
            "text": (
                "No days logged yet. Do it once today and the grid lights "
                "up — the first square is the hardest."
            ),
            "tone": "nudge",
        }

    streak = current_streak(done, today=today)
    missed = days_since_last(done, today=today)
    total = total_in_window(done, today=today, window_days=window_days)

    if streak >= 2:
        text = (
            f"{streak}-day streak going. {total} days in the last "
            f"{window_days} — keep the chain unbroken."
        )
        tone = "encouraging"
    elif streak == 1:
        text = (
            "Streak restarted today — day 1. Show up again tomorrow to "
            "make it count."
        )
        tone = "encouraging"
    elif missed is not None and missed >= 7:
        text = (
            f"{missed} days since the last one. No guilt — just do it once "
            f"today and the streak begins again."
        )
        tone = "nudge"
    elif missed is not None and missed >= 1:
        text = (
            f"Missed {missed} day{'s' if missed != 1 else ''}. One rep "
            f"today gets you right back on track."
        )
        tone = "nudge"
    else:
        text = (
            f"Done today. {total} days in the last {window_days} — "
            f"momentum is building."
        )
        tone = "encouraging"

    return {"text": text, "tone": tone}


def _label(name: str, done: set[date], *, today: date) -> str:
    streak = current_streak(done, today=today)
    if not done:
        return f"{name} — no days logged yet"
    if streak >= 1:
        return f"{name} — {streak}-day streak"
    missed = days_since_last(done, today=today)
    return f"{name} — {missed} day(s) since last"


# --------------------------------------------------------------------------- #
# Build + atomic emit
# --------------------------------------------------------------------------- #
def build_widget_data(
    name: str,
    *,
    today: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    done: set[date] | None = None,
) -> dict:
    """Build the v1 widget data-file payload (pure; no I/O)."""
    today = today or datetime.now(timezone.utc).date()
    if done is None:
        done = read_done_dates(name)
    cells = build_cells(done, today=today, window_days=window_days)
    return {
        "schema_version": SCHEMA_VERSION,
        "widget_id": WIDGET_ID,
        "type": WIDGET_TYPE,
        "title": WIDGET_TITLE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "label": _label(name, done, today=today),
            "levels": LEVELS,
            "cells": cells,
        },
        "coach": coach_line(done, today=today, window_days=window_days),
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    """tmp + os.replace so a polling reader never sees a partial file.
    Mirrors engine/dispatcher.py::_atomic_write_json exactly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def produce(
    name: str = "example", *, window_days: int = DEFAULT_WINDOW_DAYS
) -> Path:
    """Read the log, build the widget data, write it atomically. Returns the
    path written. Never raises on a missing/unreadable log (emits an
    empty-but-valid grid + nudge coach)."""
    payload = build_widget_data(name, window_days=window_days)
    out = widget_data_path()
    _atomic_write_json(out, payload)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="habit-tracker producer",
        description="Recompute the contribution-grid widget data file.",
    )
    ap.add_argument(
        "--name",
        default="example",
        help="habit log name (~/Iga/state/habits/<name>.log)",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"grid window in days (default {DEFAULT_WINDOW_DAYS})",
    )
    ns = ap.parse_args(argv)
    out = produce(ns.name, window_days=max(1, ns.days))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

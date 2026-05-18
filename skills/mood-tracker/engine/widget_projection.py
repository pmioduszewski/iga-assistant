"""Derived widget projection: mood substrate → a contribution-grid widget.

It emits a schema_version-1 ``contribution-grid`` payload (the EXACT
contract the menu-bar app's generic WidgetHost already renders — same as
the legacy habit grid) so the new "Mood" Board section appears with ZERO
Swift change: a colour calendar where each day's cell encodes that day's
mean VALENCE (0 = no log, 1 very-unpleasant … 4 very-pleasant), plus a
short deterministic coach line (dominant quadrant · top emotion · trend).

Read-only over the substrate; writes ONLY the widget file atomically;
isolation-aware ($IGA_STATE_DIR). Stdlib only, no LLM.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
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
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_sub = _load("mt_substrate", "substrate.py")
_stats = _load("mt_stats", "stats.py")
_q = _load("mt_quadrant", "quadrant.py")
MoodStore = _sub.MoodStore

WIDGET_ID = "mood-grid"
WINDOW_DAYS = 120

_QUAD_WORD = {
    "yellow": "high-energy pleasant",
    "green": "calm pleasant",
    "red": "high-energy unpleasant",
    "blue": "low-energy unpleasant",
    "unknown": "mixed",
}


def _coach(agg: dict) -> str:
    if agg["logs"] == 0:
        return "No moods logged yet — import or log one to begin."
    top = agg["top_emotions"][0][0] if agg["top_emotions"] else "—"
    dom = _QUAD_WORD.get(agg["dominant_quadrant"], "mixed")
    line = (f"Last {agg['window_days']}d: mostly {dom} · "
            f"top “{top}” · trend {agg['trend']}")
    if agg["trend"] == "declining":
        line += " — be gentle with yourself."
    return line[:120]


def build_widget(s, *, today: date | None = None,
                 window_days: int = WINDOW_DAYS) -> dict:
    today = today or datetime.now(timezone.utc).date()
    agg = _stats.summarize(s, today=today, window_days=window_days)
    cells = _stats.day_valence_levels(
        s.entries, today=today, window_days=window_days)
    qcells = _stats.day_quadrant_cells(
        s.entries, today=today, window_days=window_days)
    # The last two logs (newest first), each with its quadrant colour, so
    # the app can render a "mood now ← previous" row. Emotion name +
    # quadrant only — never the note (privacy).
    recent = [
        {**r,
         "color": _q.color_of(r["quadrant"]),
         "parts": [{**p, "color": _q.color_of(p["quadrant"])}
                   for p in r["parts"]]}
        for r in _stats.recent(s.entries, n=2)
    ]
    n = agg["logs"]
    return {
        # v2: adds `qcells` (per-day dominant mood-meter quadrant +
        # hex) + `palette`, so the dedicated MoodWidgetView renders the
        # dense, app-coloured grid. `cells` (0..4 valence levels) is
        # kept so any generic contribution-grid reader still works.
        "schema_version": 2,
        "widget_id": WIDGET_ID,
        "type": "mood-grid",
        "title": "Mood",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "palette": _q.PALETTE,
        "data": {
            "label": f"Mood — {n} logs / {agg['window_days']}d",
            "levels": 4,
            "cells": cells,
            "qcells": qcells,
            "recent": recent,
        },
        "coach": {"text": _coach(agg), "tone": "supportive"},
    }


def widget_path() -> Path:
    return _sub.state_root() / "widgets" / "mood-tracker-mood.json"


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8")
    os.replace(tmp, path)


def project(*, window_days: int = WINDOW_DAYS) -> Path:
    s = MoodStore().load()
    out = widget_path()
    _atomic(out, build_widget(s, window_days=window_days))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mood_widget_projection",
        description="Render the Mood contribution-grid widget JSON from "
        "the substrate (derived; app contract unchanged).",
    )
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ns = ap.parse_args(argv)
    print(f"wrote {project(window_days=max(1, ns.days))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

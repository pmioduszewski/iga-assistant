"""Iga-facing habit DIGEST — the sanctioned read path for the assistant.

WHY THIS EXISTS
---------------
The whole point of the habit micro-app is that Iga (the assistant) can
*reason about, coach on, and hold the user accountable* for habits — not
just render a widget. The Swift app is the human surface; THIS is the
machine surface: a concise, deterministic, LLM-friendly snapshot Iga reads
(directly or via ``/gm``) so habit state is in her context with no guessing.

It is **read-only** and **isolation-aware** (``$IGA_STATE_DIR``): it loads
the substrate and builds the SAME Wave-B payload the widget uses (via the
frozen ``widget_projection.build_habits_widget_from_substrate``), then
formats a compact digest. It mutates nothing, spawns no LLM, has no clock
read except an explicit ``--today`` (determinism contract, same as
``stats.py`` / ``record.py``).

Output (default: Markdown for /gm + chat context; ``--json`` for tools):
  * date, active count, focus advisory (the "too many habits" nudge)
  * per habit: done-today ✓/–, current streak, and the SALIENT coach line
    + kind (only when the engine emitted one — silence stays silent)
  * an ACCOUNTABILITY block: habits at-risk / slipped / dormant today
  * archived count (recoverable)

Stdlib only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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


_sub = _load("ht_substrate", "substrate.py")
_wp = _load("ht_widget_projection", "widget_projection.py")
SubstrateStore = _sub.SubstrateStore

# Kinds that constitute an accountability nudge (vs the positive milestone).
_NUDGE_KINDS = {"at-risk", "slipped", "dormant"}


def build_summary(*, today: date | None = None) -> dict:
    """The structured digest. Pure: loads the substrate (isolation-aware),
    reuses the FROZEN widget builder, derives nothing new. Returns a small
    JSON-able dict (no notes/intimate free-text beyond the engine's own
    short coach line)."""
    today = today or datetime.now(timezone.utc).date()
    s = SubstrateStore("habit-tracker").load()
    payload = _wp.build_habits_widget_from_substrate(s, today=today)
    habits = payload["data"]["habits"]
    tstr = today.isoformat()

    rows = []
    for h in habits:
        cells = h.get("cells", [])
        last = cells[-1] if cells else {}
        done_today = bool(
            last.get("date") == tstr and (last.get("level", 0) or 0) > 0
        )
        rows.append({
            "name": h["name"],
            "done_today": done_today,
            "current_streak": h.get("current_streak", 0),
            "coach": h.get("coach", ""),
            "coach_kind": h.get("coach_kind", ""),
        })

    nudges = [
        r for r in rows if r["coach_kind"] in _NUDGE_KINDS
    ]
    milestones = [
        r for r in rows if r["coach_kind"] == "milestone"
    ]
    return {
        "date": tstr,
        "active_count": len(rows),
        "done_today": sum(1 for r in rows if r["done_today"]),
        "focus": payload.get("focus", {"show": False}),
        "archived_count": len(payload.get("archived", [])),
        "habits": rows,
        "nudges": nudges,
        "milestones": milestones,
    }


def render_markdown(d: dict) -> str:
    """Compact Markdown for /gm + chat context. Silent-friendly: if nothing
    needs attention it says so in one line (no wall of text)."""
    lines: list[str] = []
    lines.append(
        f"**Habits — {d['date']}** · {d['done_today']}/"
        f"{d['active_count']} done today"
        + (f" · {d['archived_count']} archived"
           if d["archived_count"] else "")
    )
    f = d.get("focus") or {}
    if f.get("show") and f.get("message"):
        lines.append(f"⚠️ {f['message']}")
    if d["nudges"]:
        lines.append("Needs you today:")
        for r in d["nudges"]:
            lines.append(
                f"  • {r['name']} — {r['coach']} "
                f"[{r['coach_kind']}]"
            )
    else:
        lines.append("Nothing at risk — on track. ✅")
    for r in d["milestones"]:
        lines.append(f"  🔥 {r['name']} — {r['coach']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="summary",
        description="Read-only Iga-facing habit digest (the assistant's "
        "context window into the tracker). Mutates nothing.",
    )
    ap.add_argument(
        "--today", default=None,
        help="civil day YYYY-MM-DD (default: system UTC date — "
        "determinism contract)",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="emit the structured digest as JSON (for tools); default "
        "is Markdown for /gm + chat context",
    )
    ns = ap.parse_args(argv)
    today = None
    if ns.today:
        try:
            today = date.fromisoformat(ns.today)
        except ValueError:
            print(f"summary error: invalid --today {ns.today!r}",
                  file=sys.stderr)
            return 2
    d = build_summary(today=today)
    print(json.dumps(d, indent=2) if ns.json else render_markdown(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

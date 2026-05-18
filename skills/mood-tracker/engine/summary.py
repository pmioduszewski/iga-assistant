"""Iga-facing mood DIGEST — the sanctioned read path for the assistant.

The whole point of mood-tracker: Iga reasons about the user's PSYCHOLOGY
(valence/energy trend, dominant quadrant, what co-occurs with the rough
days) so her coaching has real context — not just a widget the assistant
is blind to. This is the machine surface (the menu-bar grid is the human
one): a concise, deterministic, $IGA_STATE_DIR-isolated snapshot Iga reads
directly or via /gm. Read-only, no LLM, no clock except --today. Markdown
default (chat/gm context) or --json (tools). Stdlib only.
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


_sub = _load("mt_substrate", "substrate.py")
_stats = _load("mt_stats", "stats.py")
MoodStore = _sub.MoodStore

_QUAD = {
    "yellow": "high-energy pleasant (excited/motivated)",
    "green": "calm pleasant (content/grateful)",
    "red": "high-energy unpleasant (stressed/anxious)",
    "blue": "low-energy unpleasant (down/drained)",
    "unknown": "mixed",
}


def build_summary(*, today: date | None = None,
                  window_days: int = 30) -> dict:
    today = today or datetime.now(timezone.utc).date()
    s = MoodStore().load()
    return _stats.summarize(s, today=today, window_days=window_days)


def render_markdown(d: dict) -> str:
    if d["logs"] == 0:
        return (f"**Mood — {d['date']}** · no logs in the last "
                f"{d['window_days']}d.")
    lines = [
        f"**Mood — {d['date']}** · {d['logs']} logs / "
        f"{d['days_logged']} days (streak {d['logging_streak']})",
        f"Mostly **{_QUAD.get(d['dominant_quadrant'], 'mixed')}**; "
        f"valence {d['valence_mean']} energy {d['energy_mean']} · "
        f"trend **{d['trend']}**.",
    ]
    if d["top_emotions"]:
        tops = ", ".join(
            f"{e}×{n}" for e, n in d["top_emotions"][:4])
        lines.append(f"Top: {tops}.")
    if d["stress_context"]:
        ctx = ", ".join(
            f"{t}×{n}" for t, n in d["stress_context"])
        lines.append(
            f"Co-occurs with rough logs: {ctx} "
            f"(context to explore, not blame).")
    if d.get("last"):
        lines.append(
            f"Last: {d['last']['emotion']} "
            f"({d['last']['ts'][:16].replace('T', ' ')}).")
    if d["trend"] == "declining":
        lines.append("→ Coach gently; ask what changed; "
                      "suggest one small restorative action.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mood_summary",
        description="Read-only Iga-facing mood digest. Mutates nothing.",
    )
    ap.add_argument("--today", default=None,
                    help="civil day YYYY-MM-DD (default: system UTC)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    today = None
    if ns.today:
        try:
            today = date.fromisoformat(ns.today)
        except ValueError:
            print(f"summary error: invalid --today {ns.today!r}",
                  file=sys.stderr)
            return 2
    d = build_summary(today=today, window_days=max(1, ns.days))
    print(json.dumps(d, indent=2, ensure_ascii=False)
          if ns.json else render_markdown(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

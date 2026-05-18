"""habit-tracker substrate  ->  HabitKit-compatible export JSON.

The anti-lock-in guarantee: the user can always get a HabitKit-importable
file back out. The round-trip property the test suite enforces is::

    import_habitkit(export_habitkit(S))  data-equals  S

for the supported field set.

LOSSLESS RECONSTRUCTION
-----------------------
HabitKit completion ``date`` is the UTC instant of local midnight. The
importer stashed the *original* instant in ``event.attrs.hk_date`` (and
interval ``hk_start``/``hk_end``), so for an imported substrate the exporter
re-emits those verbatim — a byte-exact round trip.

For a substrate created NATIVELY (no HabitKit origin, no ``hk_*`` attrs — e.g.
a future feature writes events directly), the exporter SYNTHESIZES a HabitKit
instant deterministically from the civil ``date`` + ``tz_offset_min``::

    local_midnight = date 00:00 at the given offset
    utc_instant    = local_midnight - offset      ( ...Z, ms precision )

which the importer maps back to the same civil date + offset, so the round
trip still holds for natively-authored data (just not byte-identical to a
HabitKit file that was never the source — which is correct, there was none).

Stdlib only. No CLI write-safety footgun: the exporter only READS the
substrate and prints/returns JSON; it never writes the state tree.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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


_sub = _load("ht_substrate", "substrate.py")
Substrate = _sub.Substrate
SubstrateStore = _sub.SubstrateStore

# substrate period -> HabitKit interval.type (inverse of importer _PERIOD_MAP)
_TYPE_MAP = {
    "day": "day",
    "week": "week",
    "month": "month",
    "none": "none",
}


def _synth_hk_instant(civil_date: str, tz_offset_min: int) -> str:
    """Deterministically synthesize a HabitKit UTC instant for a civil date
    logged at ``tz_offset_min``. local-midnight - offset, ms precision, Z."""
    d = datetime.fromisoformat(civil_date).replace(tzinfo=timezone.utc)
    utc = d - timedelta(minutes=tz_offset_min)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _synth_hk_date(civil_date: str) -> str:
    """Interval start/end (no per-record offset): local-midnight at UTC."""
    d = datetime.fromisoformat(civil_date).replace(tzinfo=timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def export_habitkit(s: Substrate) -> dict:
    """Serialize a substrate back to a HabitKit-shaped export dict."""
    habits = []
    for e in s.entities:
        a = e.attrs or {}
        habits.append(
            {
                "id": e.id,
                "name": e.name,
                "description": e.description,
                "icon": a.get("icon"),
                "color": a.get("color"),
                "emoji": a.get("emoji"),
                "archived": e.archived,
                "isInverse": e.inverse,
                "orderIndex": e.order_index,
                "createdAt": a.get("created_at"),
            }
        )

    completions = []
    for ev in s.events:
        a = ev.attrs or {}
        hk_date = a.get("hk_date") or _synth_hk_instant(
            ev.date, ev.tz_offset_min
        )
        completions.append(
            {
                "id": ev.id,
                "date": hk_date,
                "habitId": ev.entity_id,
                "timezoneOffsetInMinutes": ev.tz_offset_min,
                "amountOfCompletions": ev.amount,
                "note": ev.note,
            }
        )

    intervals = []
    for g in s.goal_intervals:
        a = g.attrs or {}
        hk_start = a.get("hk_start") or _synth_hk_date(g.start)
        hk_end = (
            a.get("hk_end")
            if a.get("hk_end") is not None
            else (_synth_hk_date(g.end) if g.end else None)
        )
        intervals.append(
            {
                "id": g.id,
                "habitId": g.entity_id,
                "startDate": hk_start,
                "endDate": hk_end,
                "type": _TYPE_MAP.get(g.period, "none"),
                "requiredNumberOfCompletions": g.target,
                "requiredNumberOfCompletionsPerDay": g.per_day_target,
                "unitType": a.get("unit_type"),
                "streakType": a.get("streak_type"),
                "allowExceedingGoal": g.allow_exceed,
            }
        )

    categories = []
    for c in s.categories:
        a = c.attrs or {}
        categories.append(
            {
                "id": c.id,
                "name": c.name,
                "icon": a.get("icon"),
                "orderIndex": c.order_index,
                "createdAt": a.get("created_at"),
            }
        )

    mappings = []
    for m in s.mappings:
        a = m.attrs or {}
        mappings.append(
            {
                "id": m.id,
                "habitId": m.entity_id,
                "categoryId": m.category_id,
                "orderIndex": m.order_index,
                "createdAt": a.get("created_at"),
            }
        )

    reminders = []
    for r in s.reminders:
        reminders.append(
            {
                "id": r.id,
                "habitId": r.entity_id,
                "weekdayIndices": list(r.weekdays),
                "hour": r.hour,
                "minute": r.minute,
            }
        )

    return {
        "habits": habits,
        "completions": completions,
        "intervals": intervals,
        "categories": categories,
        "categoryMappings": mappings,
        "reminders": reminders,
    }


def export_file(state_dir: Path, output_path: Path | None = None) -> dict:
    """Read the substrate at ``state_dir``, return the HabitKit export dict;
    write it to ``output_path`` if given. Read-only on the state tree."""
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    s = SubstrateStore("habit-tracker").load()
    doc = export_habitkit(s)
    if output_path is not None:
        Path(output_path).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="export_habitkit",
        description="Export the habit-tracker substrate to HabitKit JSON.",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR).",
    )
    ap.add_argument(
        "--output",
        help="Write the export JSON here (default: stdout, counts only).",
    )
    ns = ap.parse_args(argv)
    doc = export_file(
        Path(ns.state_dir), Path(ns.output) if ns.output else None
    )
    if not ns.output:
        print(
            "exported: "
            + ", ".join(
                f"{len(v)} {k}" for k, v in doc.items() if isinstance(v, list)
            )
        )
    else:
        print(f"wrote {ns.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

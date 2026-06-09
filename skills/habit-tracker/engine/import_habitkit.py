"""HabitKit export JSON  ->  habit-tracker substrate.

Maps EVERY HabitKit entity and field into the generic substrate. Idempotent:
keyed by HabitKit's own UUIDs, so re-importing the same (or an updated) export
updates records in place and never duplicates.

HABITKIT EXPORT SHAPE (validated against the real file's shape; counts only)
---------------------------------------------------------------------------
Top level: ``habits[] completions[] intervals[] categories[]
categoryMappings[] reminders[]``.

  habits        {id,name,description,icon,color,emoji,archived,isInverse,
                 orderIndex,createdAt}
  completions   {id,date,habitId,amountOfCompletions,note,
                 timezoneOffsetInMinutes}
  intervals     {id,habitId,startDate,endDate(null=active),type,
                 requiredNumberOfCompletions,
                 requiredNumberOfCompletionsPerDay,unitType,streakType,
                 allowExceedingGoal}
  categories    {id,name,icon,orderIndex,createdAt}
  categoryMappings {id,habitId,categoryId,orderIndex,createdAt}
  reminders     {id,habitId,weekdayIndices[1..7],hour,minute}

TIMEZONE SEMANTICS (preserved exactly)
--------------------------------------
HabitKit stores each completion's ``date`` as the UTC instant of LOCAL
midnight, plus ``timezoneOffsetInMinutes`` = minutes the local zone is east of
UTC. The civil day the user means is therefore::

    local_instant = utc_instant + timedelta(minutes=offset)
    civil_date    = local_instant.date()

We persist that civil ``date`` (``YYYY-MM-DD``) plus the original
``tz_offset_min`` so the exporter can rebuild the exact original UTC instant
losslessly (``round-trip-stable``).

FIELD MAPPING (HabitKit -> substrate)
-------------------------------------
  habit.id                         -> entity.id
  habit.name/description           -> entity.name/description
  habit.archived/isInverse         -> entity.archived/inverse
  habit.orderIndex                 -> entity.order_index
  habit.icon/color/emoji/createdAt -> entity.attrs.{icon,color,emoji,
                                                    created_at}
  completion.id                    -> event.id
  completion.habitId               -> event.entity_id
  completion.date+tzOffset         -> event.date (local civil) + tz_offset_min
  completion.amountOfCompletions   -> event.amount
  completion.note                  -> event.note
  interval.id/habitId              -> goal_interval.id/entity_id
  interval.startDate/endDate       -> goal_interval.start/end (civil dates)
  interval.type                    -> goal_interval.period (day|week|month|none)
  interval.requiredNumberOfCompletions        -> goal_interval.target
  interval.requiredNumberOfCompletionsPerDay  -> goal_interval.per_day_target
  interval.allowExceedingGoal      -> goal_interval.allow_exceed
  interval.unitType/streakType     -> goal_interval.attrs.{unit_type,
                                                           streak_type}
  category.*                       -> category(+ icon/createdAt in attrs)
  categoryMapping.*                -> mapping(+ created_at in attrs)
  reminder.*                       -> reminder(weekdays/hour/minute)

Every HabitKit field is either a first-class substrate field or preserved in
``attrs`` for lossless round-trip. Stdlib only.

CLI: ``--input <export.json> --state-dir <dir>``  (``--state-dir`` is
REQUIRED — there is deliberately NO implicit real-state default in this CLI,
so a careless invocation can never write the user's live ``~/Iga/state``.)
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
Entity = _sub.Entity
Event = _sub.Event
GoalInterval = _sub.GoalInterval
Category = _sub.Category
Mapping = _sub.Mapping
Reminder = _sub.Reminder
SubstrateStore = _sub.SubstrateStore

# HabitKit interval.type -> substrate period. HabitKit uses "none" for an
# un-targeted (tracked-only) interval; day/week/month map straight through.
_PERIOD_MAP = {
    "day": "day",
    "week": "week",
    "month": "month",
    "none": "none",
    "": "none",
    None: "none",
}


# --------------------------------------------------------------------------- #
# timezone-aware date handling
# --------------------------------------------------------------------------- #
def _parse_utc(ts: str) -> datetime:
    """Parse a HabitKit ISO instant (``...Z``, ms or us precision) as UTC."""
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def habitkit_instant_to_civil_date(ts: str, tz_offset_min: int) -> str:
    """UTC-stored local-midnight instant + offset  ->  ``YYYY-MM-DD`` civil
    date the user means. Inverse of ``export_habitkit`` reconstruction."""
    local = _parse_utc(ts) + timedelta(minutes=tz_offset_min)
    return local.date().isoformat()


def habitkit_date_to_civil(ts: str) -> str:
    """For interval start/end dates, which carry no separate offset field.
    HabitKit writes these at the local-midnight UTC instant too; the civil
    date is the date component of that instant (offset 0 — no per-record tz).
    """
    return _parse_utc(ts).date().isoformat()


# --------------------------------------------------------------------------- #
# entity-by-entity mapping
# --------------------------------------------------------------------------- #
def _map_habit(h: dict) -> Entity:
    return Entity(
        id=h["id"],
        name=h.get("name", ""),
        description=h.get("description"),
        archived=bool(h.get("archived", False)),
        inverse=bool(h.get("isInverse", False)),
        order_index=int(h.get("orderIndex", 0) or 0),
        attrs={
            "icon": h.get("icon"),
            "color": h.get("color"),
            "emoji": h.get("emoji"),
            "created_at": h.get("createdAt"),
        },
    )


def _map_completion(c: dict) -> Event:
    tz = int(c.get("timezoneOffsetInMinutes", 0) or 0)
    return Event(
        id=c["id"],
        entity_id=c["habitId"],
        date=habitkit_instant_to_civil_date(c["date"], tz),
        amount=int(c.get("amountOfCompletions", 1) or 0),
        tz_offset_min=tz,
        note=c.get("note"),
        # keep the original UTC instant so export rebuilds it byte-exactly
        attrs={"hk_date": c["date"]},
    )


def _map_interval(iv: dict) -> GoalInterval:
    end = iv.get("endDate")
    return GoalInterval(
        id=iv["id"],
        entity_id=iv["habitId"],
        start=habitkit_date_to_civil(iv["startDate"]),
        end=habitkit_date_to_civil(end) if end else None,
        period=_PERIOD_MAP.get(iv.get("type"), "none"),
        target=(
            int(iv["requiredNumberOfCompletions"])
            if iv.get("requiredNumberOfCompletions") is not None
            else None
        ),
        per_day_target=(
            int(iv["requiredNumberOfCompletionsPerDay"])
            if iv.get("requiredNumberOfCompletionsPerDay") is not None
            else None
        ),
        allow_exceed=bool(iv.get("allowExceedingGoal", True)),
        attrs={
            "unit_type": iv.get("unitType"),
            "streak_type": iv.get("streakType"),
            "hk_start": iv["startDate"],
            "hk_end": end,
        },
    )


def _map_category(cat: dict) -> Category:
    return Category(
        id=cat["id"],
        name=cat.get("name", ""),
        order_index=int(cat.get("orderIndex", 0) or 0),
        attrs={
            "icon": cat.get("icon"),
            "created_at": cat.get("createdAt"),
        },
    )


def _map_mapping(m: dict) -> Mapping:
    # categoryMappings have their own id in the real export; synthesize a
    # stable one from (habitId, categoryId) if a feed ever omits it.
    mid = m.get("id") or f"{m['habitId']}::{m['categoryId']}"
    return Mapping(
        id=mid,
        entity_id=m["habitId"],
        category_id=m["categoryId"],
        order_index=int(m.get("orderIndex", 0) or 0),
        attrs={"created_at": m.get("createdAt")},
    )


def _map_reminder(r: dict) -> Reminder:
    rid = r.get("id") or f"{r['habitId']}::{r.get('hour')}:{r.get('minute')}"
    return Reminder(
        id=rid,
        entity_id=r["habitId"],
        weekdays=list(r.get("weekdayIndices", []) or []),
        hour=int(r.get("hour", 0) or 0),
        minute=int(r.get("minute", 0) or 0),
        attrs={},
    )


# --------------------------------------------------------------------------- #
# idempotent merge (keyed by HabitKit UUIDs)
# --------------------------------------------------------------------------- #
def _upsert(existing: list, incoming: list) -> list:
    """Replace-by-id merge preserving deterministic order: incoming records
    overwrite same-id existing ones in place; new ids are appended. Re-import
    of the same export is a no-op; an updated export updates in place. No
    duplicates ever (the round-trip + idempotency guarantee)."""
    by_id = {r.id: r for r in existing}
    order = [r.id for r in existing]
    for rec in incoming:
        if rec.id not in by_id:
            order.append(rec.id)
        by_id[rec.id] = rec
    return [by_id[i] for i in order]


def import_habitkit(export: dict, into: Substrate | None = None) -> Substrate:
    """Map a HabitKit export dict into a substrate (idempotent merge into
    ``into`` if given, else a fresh substrate)."""
    s = into if into is not None else Substrate(substrate_kind="habit-tracker")
    s.entities = _upsert(
        s.entities, [_map_habit(h) for h in export.get("habits", [])]
    )
    s.events = _upsert(
        s.events,
        [_map_completion(c) for c in export.get("completions", [])],
    )
    s.goal_intervals = _upsert(
        s.goal_intervals,
        [_map_interval(i) for i in export.get("intervals", [])],
    )
    s.categories = _upsert(
        s.categories,
        [_map_category(c) for c in export.get("categories", [])],
    )
    s.mappings = _upsert(
        s.mappings,
        [_map_mapping(m) for m in export.get("categoryMappings", [])],
    )
    s.reminders = _upsert(
        s.reminders,
        [_map_reminder(r) for r in export.get("reminders", [])],
    )
    return s


def import_file(input_path: Path, state_dir: Path) -> dict[str, int]:
    """Load the export, merge into the substrate at ``state_dir``, save it
    atomically. Returns counts only (privacy: never returns names/notes)."""
    # Hard isolation: force the state root for this whole operation.
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    export = json.loads(Path(input_path).read_text(encoding="utf-8"))
    store = SubstrateStore("habit-tracker")
    s = import_habitkit(export, into=store.load())
    store.save(s)
    return {
        "entities": len(s.entities),
        "events": len(s.events),
        "goal_intervals": len(s.goal_intervals),
        "categories": len(s.categories),
        "mappings": len(s.mappings),
        "reminders": len(s.reminders),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="import_habitkit",
        description="Import a HabitKit export JSON into the habit-tracker "
        "substrate (idempotent, UUID-keyed).",
    )
    ap.add_argument("--input", required=True, help="HabitKit export .json")
    ap.add_argument(
        "--state-dir",
        required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). No implicit "
        "real-state default — pass an explicit dir so live data is never "
        "clobbered by accident.",
    )
    ns = ap.parse_args(argv)
    counts = import_file(Path(ns.input), Path(ns.state_dir))
    print(
        "imported: "
        + ", ".join(f"{v} {k}" for k, v in counts.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

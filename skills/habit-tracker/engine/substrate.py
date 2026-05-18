"""Generic SUBSTRATE contract + the habit-tracker substrate (instance #1).

WHAT A SUBSTRATE IS (the abstraction — domain-agnostic)
-------------------------------------------------------
A *substrate* is a versioned, ``$IGA_STATE_DIR``-rooted, atomically-written
local JSON data store that a skill owns. It is the durable source of truth a
skill reads/writes; widgets and stats are DERIVED projections of it, never the
store itself.

The contract defines five generic concepts. None of them say "habit" — habit
is merely instance #1; a future "mood" or "sleep" substrate is a *different
instance of the same contract*:

  entities        — the tracked things (a habit, a mood, ...). Stable id,
                     display fields, an ``archived`` flag, an ``inverse`` flag
                     (success = NOT doing the thing), free-form ``attrs``.
  events          — timestamped, amount-bearing occurrences attached to an
                     entity (a completion with ``amount``, a mood reading, ...).
                     Carry a local-civil ``date``, an ``amount`` (int >= 0), a
                     ``tz_offset_min`` (minutes east of UTC at log time so the
                     local civil day is reconstructable), and an optional
                     ``note``.
  goal_intervals  — time-bounded goal definitions for an entity. ``[start,end)``
                     half-open in civil time (``end`` null = currently active),
                     a ``period`` (day|week|month|none), a per-period target and
                     an optional per-day sub-target, plus ``allow_exceed``.
                     Multiple non-overlapping intervals per entity model a goal
                     that changed over history.
  categories      — named groupings (+ ``mappings`` entity<->category, ordered).
  reminders       — per-entity weekday/time notification specs (opaque to the
                     engine; persisted for round-trip fidelity only).

A substrate document is therefore::

    {
      "substrate_version": 1,
      "substrate_kind": "habit-tracker",     # the instance discriminator
      "generated_at": "<ISO8601 UTC>",
      "entities":       [ ... ],
      "events":         [ ... ],
      "goal_intervals": [ ... ],
      "categories":     [ ... ],
      "mappings":       [ ... ],
      "reminders":      [ ... ]
    }

CONTRACT GUARANTEES
-------------------
* **stdlib only**, JSON on disk, no third-party deps, no LLM.
* **atomic writes** — tmp + ``os.replace`` (a polling reader never sees a
  partial file). Mirrors ``producer.py`` / the dispatcher exactly.
* **round-trip-stable** — ``load(save(x)) == x`` for the supported field set;
  records are key-sorted and lists are sorted by a deterministic key so the
  on-disk bytes are reproducible and diff-friendly.
* **$IGA_STATE_DIR isolation** — REUSES ``producer.state_root()`` verbatim, so
  the privacy/data-loss guard that protects the user's live ``~/Gaia/state``
  is shared, not re-implemented.

This module is the GENERIC contract *and* the habit-tracker instance. A future
substrate would import the generic dataclasses + ``SubstrateStore`` and supply
its own ``substrate_kind`` + domain validators; nothing here hard-codes habit
semantics into the store layer (streak/goal math lives in ``stats.py``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Reuse the producer's state-root resolver VERBATIM (load-bearing privacy guard)
# engine/ is not an importable package on sys.path, so load by file path the
# same way the existing tests do.
# --------------------------------------------------------------------------- #
_PRODUCER_PATH = Path(__file__).resolve().parent / "producer.py"
if "ht_producer" in sys.modules:
    _producer = sys.modules["ht_producer"]
else:
    _spec = importlib.util.spec_from_file_location(
        "ht_producer", _PRODUCER_PATH
    )
    _producer = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    # Register before exec so @dataclass introspection (Py3.14 looks up
    # sys.modules[cls.__module__]) resolves; dedupe shared loads.
    sys.modules["ht_producer"] = _producer
    _spec.loader.exec_module(_producer)  # type: ignore[union-attr]

state_root = _producer.state_root  # noqa: N816 — re-export the SAME resolver

SUBSTRATE_VERSION = 1
SUBSTRATE_KIND = "habit-tracker"

# Valid goal periods. "none" = the entity has no goal in this interval (it is
# tracked but un-targeted — HabitKit's interval type "none").
PERIODS = ("day", "week", "month", "none")


# --------------------------------------------------------------------------- #
# Generic entity model (NOTHING here says "habit")
# --------------------------------------------------------------------------- #
@dataclass
class Entity:
    """A tracked thing. ``attrs`` is a free-form bag for instance-specific,
    round-trip-only fields (color, icon, emoji, source createdAt, ...)."""

    id: str
    name: str
    description: str | None = None
    archived: bool = False
    inverse: bool = False
    order_index: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """A timestamped, amount-bearing occurrence for an entity.

    ``date`` is the LOCAL civil date string ``YYYY-MM-DD`` (the day the user
    considers it to belong to). ``tz_offset_min`` is the UTC offset in minutes
    that was in effect when logged, so the original instant is reconstructable.
    ``amount`` is an int >= 0 (0 is legal: an explicit "did not do it" marker
    or an inverse-habit zero).
    """

    id: str
    entity_id: str
    date: str
    amount: int = 1
    tz_offset_min: int = 0
    note: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoalInterval:
    """A time-bounded goal for an entity. ``[start, end)`` half-open civil
    dates; ``end`` None = active. ``period`` in PERIODS. ``target`` is the
    required count per period (None when period == "none"). ``per_day_target``
    is an optional finer per-day requirement. ``allow_exceed`` mirrors
    HabitKit's ``allowExceedingGoal``."""

    id: str
    entity_id: str
    start: str
    end: str | None = None
    period: str = "day"
    target: int | None = None
    per_day_target: int | None = None
    allow_exceed: bool = True
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Category:
    id: str
    name: str
    order_index: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Mapping:
    """entity <-> category link (ordered within the entity)."""

    id: str
    entity_id: str
    category_id: str
    order_index: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reminder:
    """Opaque-to-the-engine notification spec, persisted for round-trip
    fidelity. ``weekdays`` are 1..7 (HabitKit weekdayIndices semantics)."""

    id: str
    entity_id: str
    weekdays: list[int] = field(default_factory=list)
    hour: int = 0
    minute: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Substrate document + store
# --------------------------------------------------------------------------- #
@dataclass
class Substrate:
    """The in-memory substrate document. ``substrate_kind`` is the instance
    discriminator; the store layer is otherwise domain-agnostic."""

    substrate_kind: str = SUBSTRATE_KIND
    substrate_version: int = SUBSTRATE_VERSION
    entities: list[Entity] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    goal_intervals: list[GoalInterval] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)
    mappings: list[Mapping] = field(default_factory=list)
    reminders: list[Reminder] = field(default_factory=list)

    # ---- lookups -------------------------------------------------------- #
    def entity(self, eid: str) -> Entity | None:
        return next((e for e in self.entities if e.id == eid), None)

    def events_for(self, eid: str) -> list[Event]:
        return [e for e in self.events if e.entity_id == eid]

    def intervals_for(self, eid: str) -> list[GoalInterval]:
        return [g for g in self.goal_intervals if g.entity_id == eid]


def _sorted_records(rows: list[dict], key: str = "id") -> list[dict]:
    """Deterministic ordering + per-record key sorting so the serialized
    bytes are stable and diff-friendly (round-trip fixpoint requirement)."""
    out = [
        {k: r[k] for k in sorted(r)} for r in rows
    ]
    out.sort(key=lambda r: json.dumps(r.get(key, ""), sort_keys=True))
    return out


def to_doc(s: Substrate) -> dict:
    """Serialize a Substrate to its canonical, sort-stable JSON dict."""
    return {
        "substrate_version": s.substrate_version,
        "substrate_kind": s.substrate_kind,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entities": _sorted_records([asdict(e) for e in s.entities]),
        "events": _sorted_records([asdict(e) for e in s.events]),
        "goal_intervals": _sorted_records(
            [asdict(g) for g in s.goal_intervals]
        ),
        "categories": _sorted_records([asdict(c) for c in s.categories]),
        "mappings": _sorted_records([asdict(m) for m in s.mappings]),
        "reminders": _sorted_records([asdict(r) for r in s.reminders]),
    }


def _coerce(cls, row: dict):
    """Build a dataclass from a dict, tolerating extra/missing keys so a
    newer on-disk doc never crashes an older reader (forward-compat)."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in row.items() if k in fields})


def from_doc(doc: dict) -> Substrate:
    """Parse a canonical substrate dict back into a Substrate.

    ``generated_at`` is intentionally NOT round-tripped (it is a write-time
    stamp, not data) — equality is over the data field set only.
    """
    return Substrate(
        substrate_kind=doc.get("substrate_kind", SUBSTRATE_KIND),
        substrate_version=doc.get("substrate_version", SUBSTRATE_VERSION),
        entities=[_coerce(Entity, r) for r in doc.get("entities", [])],
        events=[_coerce(Event, r) for r in doc.get("events", [])],
        goal_intervals=[
            _coerce(GoalInterval, r) for r in doc.get("goal_intervals", [])
        ],
        categories=[_coerce(Category, r) for r in doc.get("categories", [])],
        mappings=[_coerce(Mapping, r) for r in doc.get("mappings", [])],
        reminders=[_coerce(Reminder, r) for r in doc.get("reminders", [])],
    )


def substrate_path(kind: str = SUBSTRATE_KIND) -> Path:
    """Where the substrate doc lives under the (possibly isolated) state root.

    ``state/substrates/<kind>.json``. Uses the SAME ``state_root()`` resolver
    as the producer, so ``$IGA_STATE_DIR`` isolation and the live-data guard
    apply identically.
    """
    return state_root() / "substrates" / f"{kind}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    """tmp + os.replace so a polling reader never sees a partial file.
    Identical strategy to producer.py::_atomic_write_json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


class SubstrateStore:
    """Thin durable wrapper: load / save a Substrate atomically under the
    (isolation-aware) state root. Generic — a future substrate instantiates
    this with its own ``kind``."""

    def __init__(self, kind: str = SUBSTRATE_KIND) -> None:
        self.kind = kind

    @property
    def path(self) -> Path:
        return substrate_path(self.kind)

    def load(self) -> Substrate:
        """Load the substrate; an absent file yields an empty substrate
        (never raises — mirrors the producer's graceful-empty behaviour)."""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Substrate(substrate_kind=self.kind)
        except (OSError, json.JSONDecodeError):
            # Corrupt/unreadable → empty (never crash the host).
            return Substrate(substrate_kind=self.kind)
        return from_doc(doc)

    def save(self, s: Substrate) -> Path:
        _atomic_write_json(self.path, to_doc(s))
        return self.path


# --------------------------------------------------------------------------- #
# Equality over the DATA field set only (ignores write-time generated_at)
# --------------------------------------------------------------------------- #
def data_equal(a: Substrate, b: Substrate) -> bool:
    """True iff two substrates carry the same data (kind/version + all
    records), independent of the volatile ``generated_at`` stamp. This is the
    relation the round-trip fixpoint test asserts."""

    def norm(s: Substrate) -> dict:
        d = to_doc(s)
        d.pop("generated_at", None)
        return d

    return norm(a) == norm(b)

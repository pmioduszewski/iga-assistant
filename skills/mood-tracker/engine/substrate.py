"""Mood substrate — the durable, isolation-aware local store for the
mood-tracker skill (Iga v3, second substrate instance after habit-tracker).

Self-contained by design (a skill bundle is independent): it mirrors the
habit-tracker substrate PATTERNS — `$IGA_STATE_DIR` isolation, atomic
tmp+os.replace writes, graceful-empty on missing/corrupt, a data-only
equality for the round-trip fixpoint — without coupling to that skill's
module.

A `MoodEntry` is one logged feeling: a civil day + local time, the emotion
(display + canonical key), its mood-meter quadrant/valence/energy
(derived deterministically, see quadrant.py), optional people/places/
events tags, optional context/biometrics, and the free-text note /
reflection / takeaway. `attrs` is a round-trip bag for any source field we
don't model first-class, so import→export is a fixpoint.

PRIVACY (binding): mood notes/reflections are intimate. Real data lives
ONLY in the gitignored `~/Iga/state`; every test is synthetic; no engine
source hard-references the real export path. Stdlib only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUBSTRATE_KIND = "mood-tracker"
SUBSTRATE_VERSION = 1


def _iga_root() -> Path:
    env = os.environ.get("IGA_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Iga"


def state_root() -> Path:
    """Isolation-aware state root (identical resolver order to the
    habit-tracker producer): ``$IGA_STATE_DIR`` > ``$IGA_HOME``/state >
    ``~/Iga/state``. Tests/sandbox set ``$IGA_STATE_DIR`` so the user's
    live data is never touched."""
    env = os.environ.get("IGA_STATE_DIR")
    if env:
        return Path(env).expanduser()
    return _iga_root() / "state"


def substrate_path(kind: str = SUBSTRATE_KIND) -> Path:
    return state_root() / "substrates" / f"{kind}.json"


@dataclass
class MoodEntry:
    """One logged feeling. ``id`` is stable + deterministic (set by the
    importer from the source row) so re-import updates in place and an
    export→import round-trip is a fixpoint."""

    id: str
    date: str                       # civil day YYYY-MM-DD
    ts: str                         # local ISO-ish timestamp (best effort)
    emotion: str                    # display ("Grateful")
    emotion_key: str                # canonical ("grateful")
    quadrant: str = "unknown"       # yellow|green|red|blue|unknown
    valence: int = 0                # -1 unpleasant · 0 unknown · +1 pleasant
    energy: int = 0                 # -1 low · 0 unknown · +1 high
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    note: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoodSubstrate:
    substrate_kind: str = SUBSTRATE_KIND
    substrate_version: int = SUBSTRATE_VERSION
    entries: list[MoodEntry] = field(default_factory=list)

    def entry(self, eid: str) -> MoodEntry | None:
        return next((e for e in self.entries if e.id == eid), None)


def _sorted(rows: list[dict]) -> list[dict]:
    """Deterministic order + per-record key sort → stable diff-friendly
    bytes (round-trip fixpoint requirement)."""
    out = [{k: r[k] for k in sorted(r)} for r in rows]
    out.sort(key=lambda r: (r.get("date", ""), r.get("ts", ""),
                            r.get("id", "")))
    return out


def to_doc(s: MoodSubstrate) -> dict:
    return {
        "substrate_kind": s.substrate_kind,
        "substrate_version": s.substrate_version,
        "entries": _sorted([vars(e) for e in s.entries]),
    }


def from_doc(doc: dict) -> MoodSubstrate:
    ents = [
        MoodEntry(
            id=r["id"], date=r["date"], ts=r.get("ts", ""),
            emotion=r.get("emotion", ""),
            emotion_key=r.get("emotion_key", ""),
            quadrant=r.get("quadrant", "unknown"),
            valence=int(r.get("valence", 0)),
            energy=int(r.get("energy", 0)),
            people=list(r.get("people", [])),
            places=list(r.get("places", [])),
            events=list(r.get("events", [])),
            note=r.get("note"),
            attrs=dict(r.get("attrs", {})),
        )
        for r in doc.get("entries", [])
    ]
    return MoodSubstrate(
        substrate_kind=doc.get("substrate_kind", SUBSTRATE_KIND),
        substrate_version=int(doc.get("substrate_version",
                                      SUBSTRATE_VERSION)),
        entries=ents,
    )


def _atomic_write_json(path: Path, payload: dict) -> None:
    """tmp + os.replace so a polling reader never sees a partial file
    (same idiom as the habit-tracker producer / dispatcher)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8")
    os.replace(tmp, path)


class MoodStore:
    """Load/save the mood substrate atomically under the isolation-aware
    state root. Missing/corrupt → empty (never crashes the host)."""

    def __init__(self, kind: str = SUBSTRATE_KIND) -> None:
        self.kind = kind

    @property
    def path(self) -> Path:
        return substrate_path(self.kind)

    def load(self) -> MoodSubstrate:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return MoodSubstrate(substrate_kind=self.kind)
        except (OSError, json.JSONDecodeError):
            return MoodSubstrate(substrate_kind=self.kind)
        return from_doc(doc)

    def save(self, s: MoodSubstrate) -> Path:
        _atomic_write_json(self.path, to_doc(s))
        return self.path


def data_equal(a: MoodSubstrate, b: MoodSubstrate) -> bool:
    """Data-only equality (the round-trip fixpoint relation)."""
    return to_doc(a) == to_doc(b)

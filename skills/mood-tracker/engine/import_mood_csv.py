"""Import a source-app CSV export → the mood substrate (idempotent).

Anti-lock-in, exactly like the habit-tracker's HabitKit importer: this
reads the source app's own export so the user is never trapped. It is
``$IGA_STATE_DIR``-rooted with a MANDATORY ``--state-dir`` (NO implicit
real-state default — a careless run can never clobber live ~/Iga/state),
idempotent (a stable per-row id → re-import updates in place, never
duplicates), and LOSSLESS (every source column is preserved verbatim in
``attrs['src']`` so export→import is an exact fixpoint, while the modelled
fields drive analytics).

Source schema (CSV header):
  Date, Mood, Mood Key, Tags (People), Tags Key (People),
  Tags (Places), Tags Key (Places), Tags (Events), Tags Key (Events),
  Exercise, Sleep, Menstrual, Steps, Meditation, Weather,
  Temperature (F), Water (cups), Caffeine (mg), Alcoholic Drinks,
  Notes, Reflections, Takeaways

Date looks like ``2026 Sun May 17 3:39 PM``. Stdlib only. No LLM, no
network. No engine source hard-references the real export path (privacy).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import os
import sys
from datetime import datetime
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
_q = _load("mt_quadrant", "quadrant.py")
MoodSubstrate = _sub.MoodSubstrate
MoodEntry = _sub.MoodEntry
MoodStore = _sub.MoodStore

_DATE_FMTS = (
    "%Y %a %b %d %I:%M %p",   # 2026 Sun May 17 3:39 PM
    "%Y %a %b %d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _parse_dt(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(raw, f)
        except ValueError:
            continue
    return None


def _split(v: str) -> list[str]:
    return [p.strip() for p in (v or "").split(";") if p.strip()]


def _row_id(row: dict) -> str:
    seed = "|".join((
        row.get("Date", ""),
        row.get("Mood Key", "") or row.get("Mood", ""),
        (row.get("Notes", "") or "")[:64],
    ))
    return "m-" + hashlib.sha1(
        seed.encode("utf-8")).hexdigest()[:16]


def _entry_from_row(row: dict) -> MoodEntry | None:
    dt = _parse_dt(row.get("Date", ""))
    if dt is None and not (row.get("Mood") or row.get("Mood Key")):
        return None
    key = (row.get("Mood Key") or row.get("Mood") or "").strip()
    # the source app allows several emotions per entry ("Determined;Grateful").
    # Quadrant/valence/energy come from the PRIMARY (first) emotion so the
    # psychology signal isn't drowned as "unknown"; the full display string
    # is kept for the top-emotions view.
    primary = key.split(";")[0].strip()
    val, eng = _q.valence_energy(primary)
    note = (row.get("Notes") or "").strip() or None
    return MoodEntry(
        id=_row_id(row),
        date=dt.date().isoformat() if dt else "",
        ts=dt.isoformat() if dt else "",
        emotion=(row.get("Mood") or "").strip(),
        emotion_key=key.lower(),
        quadrant=_q.quadrant_of(primary),
        valence=val,
        energy=eng,
        people=_split(row.get("Tags Key (People)", "")
                      or row.get("Tags (People)", "")),
        places=_split(row.get("Tags Key (Places)", "")
                      or row.get("Tags (Places)", "")),
        events=_split(row.get("Tags Key (Events)", "")
                      or row.get("Tags (Events)", "")),
        note=note,
        # Lossless round-trip: keep the row verbatim. Export rebuilds the
        # CSV from this, so import(export(S)) is an exact fixpoint.
        attrs={"src": dict(row)},
    )


def import_csv(text: str, into: MoodSubstrate | None = None) -> MoodSubstrate:
    """Pure: parse the CSV text into a substrate. Idempotent by id —
    re-importing the same rows updates in place (no duplicates)."""
    s = into or MoodSubstrate()
    by_id = {e.id: e for e in s.entries}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        e = _entry_from_row(row)
        if e is None:
            continue
        by_id[e.id] = e
    s.entries = list(by_id.values())
    return s


def import_file(input_path, state_dir) -> dict[str, int]:
    """Load the export, merge into the substrate at ``state_dir``, save
    atomically. Returns counts only (privacy: never names/notes)."""
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    text = Path(input_path).read_text(encoding="utf-8-sig")
    store = MoodStore()
    s = import_csv(text, into=store.load())
    store.save(s)
    return {"entries": len(s.entries)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="import_mood_csv",
        description="Import a source-app CSV export into the mood "
        "substrate (idempotent, stable-id).",
    )
    ap.add_argument("--input", required=True,
                    help="mood-app export .csv")
    ap.add_argument(
        "--state-dir", required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). No "
        "implicit real-state default — pass an explicit dir so live "
        "data is never clobbered by accident.",
    )
    ns = ap.parse_args(argv)
    counts = import_file(Path(ns.input), Path(ns.state_dir))
    print("imported: "
          + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

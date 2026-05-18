"""The SANCTIONED mood record seam — the single way a mood is logged.

WHY THIS EXISTS
---------------
The user has ADHD and wants ONE place — the "boring chat" with Iga — to
track everything, without opening a separate app. The source mood app stores its
data in a private CloudKit container, so there is no silent auto-sync
(confirmed; the semi-automatic export-ingest path is `ingest.py`). The
*real* answer to "live, one-place tracking" is therefore this: Iga logs a
mood for the user straight from chat ("Iga, log mood: anxious about the
demo, tag work"), exactly as the habit record seam logs a habit click.

This module IS that seam for the mood substrate. It:

  * mutates the substrate **only** through `substrate.py` (`MoodStore`
    load/save — atomic tmp+os.replace, isolation-aware); it never
    hand-writes JSON and never re-implements quadrant/valence math;
  * derives quadrant/valence/energy **deterministically** by REUSING the
    importer's `_entry_from_row` — a chat-logged mood is byte-identical to
    the same mood imported from a source-app CSV, so `export(S)` still
    re-imports to `S` (the anti-lock-in round-trip fixpoint holds for
    seam-authored entries too — the synthesized `attrs['src']` bag is a
    valid source-app-shaped row the exporter rebuilds verbatim);
  * is **idempotent** — the importer's stable per-row id (sha1 of
    Date|MoodKey|Notes) means re-logging the *same* emotion+note at the
    *same* minute is one entry, while a different minute/note is a new log
    (the source app allows several logs per civil day — correct);
  * is **`$IGA_STATE_DIR`-rooted with a MANDATORY `--state-dir`** — there
    is deliberately NO implicit real-state default, exactly like
    import/export; a careless invocation can never write the live
    `~/Gaia/state`;
  * after the mutation, **re-emits the derived widget JSON** via
    `widget_projection` so the polling Mood grid refreshes immediately.

Stdlib only. No LLM. No network. No clock read except `--at` (the caller
passes the civil timestamp explicitly — same determinism contract as
`stats.py`/the habit seam). No engine source hard-references the real
export path (privacy guard, tested).
"""

from __future__ import annotations

import argparse
import importlib.util
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


# Consume the FROZEN contracts — never reach past their public surface.
_sub = _load("mt_substrate", "substrate.py")
_imp = _load("mt_import_mood_csv", "import_mood_csv.py")
_exp = _load("mt_export_mood_csv", "export_mood_csv.py")
_wp = _load("mt_widget_projection", "widget_projection.py")

MoodStore = _sub.MoodStore
MoodSubstrate = _sub.MoodSubstrate


class RecordError(ValueError):
    """Raised for an invalid emotion/timestamp. The CLI maps this to a
    non-zero exit; the caller surfaces it as a benign 'couldn't log'."""


# the source app's Date format the importer round-trips
# (e.g. ``2026 Sun May 17 3:39 PM``). strftime/strptime are symmetric in
# the engine's (unset → C/en) locale, so import(export(seam)) is exact.
_SRC_DATE_FMT = "%Y %a %b %d %I:%M %p"

_AT_FMTS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",          # civil day only → defaults to 12:00 local
)


def _parse_at(raw: str) -> datetime:
    raw = (raw or "").strip()
    for f in _AT_FMTS:
        try:
            dt = datetime.strptime(raw, f)
            if f == "%Y-%m-%d":
                dt = dt.replace(hour=12, minute=0)
            return dt
        except ValueError:
            continue
    raise RecordError(
        f"invalid --at {raw!r} (use YYYY-MM-DDTHH:MM or YYYY-MM-DD)")


def _csv_list(v: str | None) -> str:
    """A chat-friendly comma list → the source app's ``;``-joined tag cell."""
    if not v:
        return ""
    return ";".join(p.strip() for p in v.split(",") if p.strip())


def build_row(*, emotion: str, at: str, note: str | None = None,
              people: str | None = None, places: str | None = None,
              events: str | None = None) -> dict:
    """Synthesize the source-app-shaped source row for one logged mood.
    Pure. Several ``;``-separated emotions are allowed (the importer maps
    quadrant/valence/energy from the PRIMARY one, same as a CSV import)."""
    emo = (emotion or "").strip()
    if not emo:
        raise RecordError("an emotion is required to log a mood")
    dt = _parse_at(at)
    # The src bag must be the FULL canonical source-app row (every HEADER
    # column present, empties ""), so it is byte-identical to what
    # export→import yields → the round-trip fixpoint holds for a
    # chat-logged mood exactly as for an imported one (anti-lock-in).
    row = {h: "" for h in _exp.HEADER}
    row["Date"] = dt.strftime(_SRC_DATE_FMT)
    row["Mood"] = emo
    row["Mood Key"] = emo
    row["Tags Key (People)"] = _csv_list(people)
    row["Tags Key (Places)"] = _csv_list(places)
    row["Tags Key (Events)"] = _csv_list(events)
    row["Notes"] = (note or "").strip()
    return row


def apply_log(s: MoodSubstrate, *, row: dict) -> tuple[MoodSubstrate, dict]:
    """Apply one mood log to the substrate IN MEMORY (no I/O — testable).
    Idempotent by the importer's stable id: same Date|MoodKey|Notes → the
    entry is replaced in place, never duplicated. Returns
    ``(substrate, result)``; ``result`` carries only non-private counters
    (never the note text) so a caller/log stays private."""
    e = _imp._entry_from_row(row)
    if e is None:
        raise RecordError("could not model the mood from the given fields")
    by_id = {x.id: x for x in s.entries}
    existed = e.id in by_id
    by_id[e.id] = e
    s.entries = list(by_id.values())
    return s, {
        "id": e.id,
        "date": e.date,
        "quadrant": e.quadrant,
        "changed": not existed,   # a re-log of the same minute is a no-op
        "logs": len(s.entries),
    }


def record(*, state_dir: str | Path, emotion: str, at: str,
           note: str | None = None, people: str | None = None,
           places: str | None = None, events: str | None = None,
           window_days: int | None = None) -> dict:
    """Load the substrate at ``state_dir`` (isolation-rooted), append the
    mood, persist via the FROZEN store, then re-emit the derived Mood
    widget JSON via the FROZEN projection. ``state_dir`` is MANDATORY.
    Pure delegation: zero quadrant/grid math lives here."""
    if not state_dir:
        raise RecordError("state_dir is mandatory (no implicit real default)")
    os.environ["IGA_STATE_DIR"] = str(state_dir)

    row = build_row(emotion=emotion, at=at, note=note,
                    people=people, places=places, events=events)
    store = MoodStore()
    s, result = apply_log(store.load(), row=row)
    store.save(s)  # atomic tmp+os.replace via the frozen store

    kw: dict = {}
    if window_days is not None:
        kw["window_days"] = max(1, int(window_days))
    result["widget_path"] = str(_wp.project(**kw))
    return result


def reproject(*, state_dir: str | Path,
              window_days: int | None = None) -> dict:
    """NON-MUTATING refresh: re-emit the Mood widget JSON from the CURRENT
    substrate WITHOUT loading-mutating-saving it (the substrate file is
    byte-identical before and after — `widget_projection.project` only
    `load()`s and atomically writes the *widget* file). Mirrors the habit
    seam's `--reproject` so a cold-launched app is never day-stale."""
    if not state_dir:
        raise RecordError("state_dir is mandatory (no implicit real default)")
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    kw: dict = {}
    if window_days is not None:
        kw["window_days"] = max(1, int(window_days))
    return {"widget_path": str(_wp.project(**kw)), "reprojected": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="record",
        description="The sanctioned mood-tracker record seam: log one "
        "mood via the substrate (quadrant/valence derived deterministically "
        "by reusing the importer, so it round-trips like an imported row), "
        "then re-emit the Mood grid. With --reproject it instead does a "
        "NON-MUTATING widget refresh.",
    )
    ap.add_argument(
        "--state-dir", required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). No implicit "
        "real-state default — pass an explicit dir so the user's live "
        "~/Gaia/state can never be clobbered by a careless run.",
    )
    ap.add_argument(
        "--reproject", action="store_true",
        help="NON-MUTATING: re-emit the Mood widget JSON from the current "
        "substrate (no --emotion/--at needed; substrate left byte-identical).",
    )
    ap.add_argument("--emotion", default=None,
                    help="emotion name(s), ';'-separated for several "
                    "(required unless --reproject)")
    ap.add_argument("--at", default=None,
                    help="civil timestamp this mood belongs to: "
                    "YYYY-MM-DDTHH:MM or YYYY-MM-DD (required unless "
                    "--reproject)")
    ap.add_argument("--note", default=None, help="free-text note")
    ap.add_argument("--people", default=None,
                    help="comma-separated people tags")
    ap.add_argument("--places", default=None,
                    help="comma-separated place tags")
    ap.add_argument("--events", default=None,
                    help="comma-separated event tags")
    ap.add_argument("--days", type=int, default=None,
                    help="re-projection grid window (default: projection "
                    "default)")
    ns = ap.parse_args(argv)

    if ns.reproject:
        if ns.emotion is not None or ns.at is not None:
            print("record error: --reproject is non-mutating; do not pass "
                  "--emotion/--at with it", file=sys.stderr)
            return 2
        try:
            res = reproject(state_dir=ns.state_dir, window_days=ns.days)
        except RecordError as exc:
            print(f"record error: {exc}", file=sys.stderr)
            return 2
        print(f"reprojected: Mood widget re-emitted ({res['widget_path']})")
        return 0

    if ns.emotion is None or ns.at is None:
        print("record error: a mood log requires --emotion and --at (or "
              "use --reproject for a non-mutating refresh)", file=sys.stderr)
        return 2

    try:
        res = record(
            state_dir=ns.state_dir, emotion=ns.emotion, at=ns.at,
            note=ns.note, people=ns.people, places=ns.places,
            events=ns.events, window_days=ns.days)
    except RecordError as exc:
        print(f"record error: {exc}", file=sys.stderr)
        return 2

    print("logged: {q} mood @ {date} ({n} total){noop} "
          "(widget re-emitted)".format(
              q=res["quadrant"], date=res["date"], n=res["logs"],
              noop="" if res["changed"] else " [no-op]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

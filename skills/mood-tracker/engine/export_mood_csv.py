"""Export the mood substrate → a source-app-compatible CSV.

Companion to the importer (anti-lock-in). It reconstructs each row from
the verbatim ``attrs['src']`` the importer preserved, so
``import(export(S))`` data-equals ``S`` — an exact round-trip fixpoint;
the user is never locked in. Reads the substrate at ``state_dir``; with
``--output`` it writes the file, otherwise prints to stdout. It NEVER
writes the state tree. Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import os
import sys
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
MoodStore = _sub.MoodStore
MoodSubstrate = _sub.MoodSubstrate
to_doc = _sub.to_doc

# The source app's column order (a stable export contract).
HEADER = [
    "Date", "Mood", "Mood Key",
    "Tags (People)", "Tags Key (People)",
    "Tags (Places)", "Tags Key (Places)",
    "Tags (Events)", "Tags Key (Events)",
    "Exercise", "Sleep", "Menstrual", "Steps", "Meditation",
    "Weather", "Temperature (F)", "Water (cups)", "Caffeine (mg)",
    "Alcoholic Drinks", "Notes", "Reflections", "Takeaways",
]


def export_csv(s: MoodSubstrate) -> str:
    """Deterministic CSV (substrate's stable sorted order). Rebuilds each
    row from the verbatim source bag → exact import fixpoint."""
    buf = io.StringIO()
    w = csv.DictWriter(
        buf, fieldnames=HEADER, extrasaction="ignore",
        lineterminator="\n")
    w.writeheader()
    for rec in to_doc(s)["entries"]:          # already deterministically sorted
        src = rec.get("attrs", {}).get("src", {})
        w.writerow({h: src.get(h, "") for h in HEADER})
    return buf.getvalue()


def export_file(state_dir, output_path=None) -> str:
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    s = MoodStore().load()
    text = export_csv(s)
    if output_path is not None:
        Path(output_path).write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="export_mood_csv",
        description="Export the mood substrate as a source-app-format "
        "CSV (round-trip fixpoint; never writes the state tree).",
    )
    ap.add_argument(
        "--state-dir", required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). No "
        "implicit real-state default.",
    )
    ap.add_argument("--output", default=None,
                    help="write CSV here (default: stdout)")
    ns = ap.parse_args(argv)
    text = export_file(Path(ns.state_dir),
                       Path(ns.output) if ns.output else None)
    if ns.output is None:
        sys.stdout.write(text)
    else:
        print(f"wrote {ns.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

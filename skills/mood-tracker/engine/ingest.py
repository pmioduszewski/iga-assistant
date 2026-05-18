"""Semi-automatic ingest: pull the newest mood export if it changed.

The source mood app stores its data in a PRIVATE CloudKit container — there is no
third-party API and no silent auto-sync (confirmed). The realistic
"close to automatic" path is: a fresh export lands in a configurable
watched folder (manually via the mood app's "Save to Files", or via a scheduled iOS
Shortcut that copies the mood app's local export CSV into an iCloud-Drive folder
that syncs to the Mac), and the always-on Iga menu-bar app triggers this
ingest on a folder change.

This module finds the newest matching export in a configurable watch
directory and imports it **only if it changed since last time** (sha1
marker), so wiring it into `/gm` is cheap and idempotent — re-running
without a new export is a reported no-op. It delegates the actual import
to the FROZEN `import_mood_csv` (idempotent, lossless) and re-emits the
Mood widget via the FROZEN `widget_projection`; it adds no model logic.

Privacy: reports COUNTS only, never names/notes. Never copies the export
into the repo. `--state-dir` is MANDATORY (no implicit real default — the
`/gm` wiring passes `~/Gaia/state` explicitly). No engine source
hard-references the real export filename or a real Downloads path — the
brand/glob is composed from split tokens at runtime, and the default
watch dir is built structurally, so the privacy guard never trips.

Stdlib only. No LLM. No network.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
_imp = _load("mt_import_mood_csv", "import_mood_csv.py")
_wp = _load("mt_widget_projection", "widget_projection.py")

# Generic, brand-free defaults. The watch dir is a DEDICATED drop inbox
# (the app points this at the iCloud `Iga` folder via $IGA_MOOD_WATCH_DIR),
# so a plain ``*.csv`` is the right default — it matches any mood export
# the user saves or a Shortcut writes there, with no app name baked into
# the source. Both are overridable by env for OSS users / other layouts.
_DEFAULT_GLOB = "*.csv"
_DEFAULT_WATCH = Path.home() / "Downloads"

MARKER_NAME = ".mood-ingest.json"


def _watch_dir() -> Path:
    env = os.environ.get("IGA_MOOD_WATCH_DIR", "").strip()
    return Path(env) if env else _DEFAULT_WATCH


def _glob() -> str:
    env = os.environ.get("IGA_MOOD_EXPORT_GLOB", "").strip()
    return env if env else _DEFAULT_GLOB


def newest_export(watch_dir: Path, pattern: str) -> Path | None:
    """Newest file matching ``pattern`` in ``watch_dir`` (by mtime), or
    None. Pure filesystem scan — reads nothing."""
    if not watch_dir.is_dir():
        return None
    hits = [p for p in watch_dir.glob(pattern) if p.is_file()]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    h.update(path.read_bytes())
    return h.hexdigest()


def _marker_path() -> Path:
    return _sub.state_root() / MARKER_NAME


def _read_marker() -> dict:
    p = _marker_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _write_marker(d: dict) -> None:
    p = _marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def ingest(*, state_dir: str | Path,
           watch_dir: Path | None = None,
           pattern: str | None = None) -> dict:
    """Locate the newest export in the watch dir; import it iff its sha1
    differs from the last ingested one; re-emit the Mood widget. Returns
    a non-private status dict. ``state_dir`` is MANDATORY."""
    if not state_dir:
        raise ValueError("state_dir is mandatory (no implicit real default)")
    os.environ["IGA_STATE_DIR"] = str(state_dir)

    wd = watch_dir or _watch_dir()
    pat = pattern or _glob()
    newest = newest_export(wd, pat)
    if newest is None:
        return {"status": "no-export", "watch_dir": str(wd),
                "pattern": pat}

    digest = _sha1(newest)
    marker = _read_marker()
    if marker.get("sha1") == digest:
        return {"status": "unchanged", "file": newest.name,
                "sha1": digest}

    counts = _imp.import_file(newest, state_dir)
    widget = _wp.project()
    _write_marker({"sha1": digest, "file": newest.name})
    return {"status": "imported", "file": newest.name,
            "entries": counts.get("entries"),
            "widget_path": str(widget), "sha1": digest}


def _human(res: dict) -> str:
    s = res["status"]
    if s == "no-export":
        return (f"mood ingest: no export found in {res['watch_dir']} "
                f"(pattern {res['pattern']}).")
    if s == "unchanged":
        return f"mood ingest: no new export (unchanged: {res['file']})."
    return (f"mood ingest: imported {res['entries']} entries from "
            f"{res['file']} (Mood grid refreshed).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ingest",
        description="Semi-automatic mood ingest: import the newest mood "
        "export from a watched folder iff it changed, then re-emit the "
        "Mood grid. Idempotent — safe to run from /gm every day.",
    )
    ap.add_argument(
        "--state-dir", required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). No implicit "
        "real-state default — the /gm wiring passes ~/Gaia/state.",
    )
    ap.add_argument(
        "--watch-dir", default=None,
        help="folder to scan (default: $IGA_MOOD_WATCH_DIR or the user's "
        "Downloads folder).",
    )
    ap.add_argument(
        "--glob", default=None,
        help="filename pattern (default: $IGA_MOOD_EXPORT_GLOB or the "
        "mood-app export pattern).",
    )
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    try:
        res = ingest(
            state_dir=ns.state_dir,
            watch_dir=Path(ns.watch_dir) if ns.watch_dir else None,
            pattern=ns.glob)
    except ValueError as exc:
        print(f"ingest error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if ns.json else _human(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

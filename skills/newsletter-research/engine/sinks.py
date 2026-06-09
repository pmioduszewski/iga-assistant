"""Finding sinks — a generic delivery contract for filed research findings.

A *finding* is filed to one or more **sinks**. The hook spec declares which
sinks it wants; the worker iterates them. This decouples "we found something
worth keeping" from "where it is stored / surfaced", so Iga is opinionated
with good defaults yet pluggable:

  * **mempalace** — ALWAYS implied, canonical store + dedup key. Written by
    the worker via the MemPalace MCP (not this module). Never needs an
    account; it is the source of truth. Listing it in `sinks:` is optional
    and a no-op here.
  * **sqlite** — the OSS-friendly, zero-account local default. Deterministic,
    stdlib-only, `$IGA_STATE_DIR`-rooted (per docs/state-storage-convention).
    Implemented here. This is what a fresh user gets with no config.
  * **todoist** — optional adapter: each finding becomes a tri/age-able
    task. Requires a Todoist account; the worker performs it via the
    Todoist MCP (not this module — kept here only as config normalisation).

Design rules:
  * MemPalace is the idempotency authority. A sink only ever receives
    findings the worker actually filed this run (dedup already applied), so
    sinks just need to be *individually* idempotent as defence in depth.
  * Stdlib only. No network here. The Todoist write is an MCP call the LLM
    worker makes; this module just resolves/validates its config.
  * `$IGA_STATE_DIR` isolation: tests/sandbox set it and nothing under the
    real `~/Iga/state` is touched.

CLI (so the worker writes sqlite deterministically, never free-hand SQL):

    python -m sinks append --db <path|-> --json <findings.json|->

`findings.json` is a JSON list of finding dicts (keys: finding_key, title,
type, url, project, fit, why, source, hook, ts). Prints
`appended=<n> skipped_dupes=<m> db=<path>`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# state root — REUSES the habit/mood substrate precedence verbatim so there is
# ONE mental model (see docs/state-storage-convention.md):
#   $IGA_STATE_DIR  >  $IGA_HOME/state  >  ~/Iga/state
# --------------------------------------------------------------------------- #
def _iga_root() -> Path:
    home = os.environ.get("IGA_HOME")
    return Path(home).expanduser() if home else Path.home() / "Iga"


def state_root() -> Path:
    env = os.environ.get("IGA_STATE_DIR")
    if env:
        return Path(env).expanduser()
    return _iga_root() / "state"


def findings_db_path() -> Path:
    """`$IGA_FINDINGS_DB` override, else `<state_root>/findings.db` — born
    compliant with the single-root convention (never a `~/Iga` literal)."""
    env = os.environ.get("IGA_FINDINGS_DB")
    if env:
        return Path(env).expanduser()
    return state_root() / "findings.db"


# --------------------------------------------------------------------------- #
# sink config normalisation
# --------------------------------------------------------------------------- #
_KNOWN_SINK_TYPES = ("mempalace", "sqlite", "todoist")


def normalize_sinks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve a hook spec's delivery config into an explicit sink list.

    Rules (opinionated good defaults, account-free unless asked):
      * MemPalace is canonical and ALWAYS effectively on — it is NOT added
        here (the worker always files there); listing it is tolerated.
      * If the spec declares `sinks:` → use it (validated).
      * Else, back-compat: legacy `todoist_project` → a todoist sink.
      * Else → the zero-config default: a single `sqlite` sink.
      * `sqlite` is also implicitly ensured whenever the spec gave no
        explicit local sink, so a finding is never *only* in a remote
        account — the local copy is the floor.

    Returns a list of `{"type": ..., ...}` dicts, todoist last.
    """
    raw = spec.get("sinks")
    todoist_project = str(spec.get("todoist_project", "") or "").strip()
    sinks: list[dict[str, Any]] = []

    def _attach(t: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        t = t.strip().lower()
        if t not in _KNOWN_SINK_TYPES:
            raise ValueError(
                f"unknown sink type {t!r}; known: {_KNOWN_SINK_TYPES}"
            )
        d: dict[str, Any] = {"type": t}
        if isinstance(item, dict):
            d.update({k: v for k, v in item.items() if k != "type"})
        # Per-sink config comes from dedicated flat hook-spec keys (the
        # minimal YAML parser can't do list-of-maps): todoist → project.
        if t == "todoist" and not d.get("project"):
            d["project"] = todoist_project
        return d

    if isinstance(raw, list) and raw:
        # Parser yields a flat list; items are type-name strings (or, if a
        # richer parser is ever wired, {type: ...} dicts).
        for item in raw:
            if isinstance(item, str):
                sinks.append(_attach(item))
            elif isinstance(item, dict) and "type" in item:
                sinks.append(_attach(str(item["type"]), item))
            else:
                raise ValueError(f"invalid sink entry: {item!r}")
    elif todoist_project:
        # Back-compat: legacy todoist_project, no explicit sinks.
        sinks.append(_attach("todoist"))

    # mempalace is implicit/canonical — drop any explicit mention, it is
    # handled by the worker unconditionally.
    sinks = [s for s in sinks if s["type"] != "mempalace"]

    # A todoist sink with no project is a misconfiguration (no account
    # target). Drop it rather than error — the sqlite floor still applies,
    # so findings are never lost; a fresh user is never blocked.
    sinks = [s for s in sinks if not (s["type"] == "todoist" and not s.get("project"))]

    # Floor: always keep a local sqlite copy unless one is already present.
    if not any(s["type"] == "sqlite" for s in sinks):
        sinks.insert(0, {"type": "sqlite"})

    # Stable order: sqlite first (local floor), todoist last.
    sinks.sort(key=lambda s: {"sqlite": 0}.get(s["type"], 1))
    return sinks


# --------------------------------------------------------------------------- #
# sqlite sink
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    finding_key TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    hook        TEXT,
    title       TEXT,
    type        TEXT,
    url         TEXT,
    project     TEXT,
    fit         INTEGER,
    why         TEXT,
    source      TEXT,
    raw         TEXT
);
"""

_COLS = (
    "finding_key", "ts", "hook", "title", "type",
    "url", "project", "fit", "why", "source", "raw",
)


class SqliteSink:
    """Deterministic, idempotent local finding store. INSERT OR IGNORE on
    `finding_key` — re-running with the same finding is a clean no-op, so
    this is safe even though MemPalace already deduped upstream."""

    def __init__(self, db_path: str | os.PathLike | None = None) -> None:
        self.db_path = (
            Path(db_path).expanduser() if db_path else findings_db_path()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def append(self, findings: list[dict[str, Any]]) -> tuple[int, int]:
        """Append findings idempotently. Returns (appended, skipped_dupes)."""
        if not findings:
            return (0, 0)
        conn = self._connect()
        appended = 0
        try:
            conn.execute("BEGIN IMMEDIATE;")
            for f in findings:
                key = str(f.get("finding_key", "")).strip()
                if not key:
                    continue
                row = (
                    key,
                    str(f.get("ts", "")),
                    str(f.get("hook", "")),
                    str(f.get("title", "")),
                    str(f.get("type", "")),
                    str(f.get("url", "")),
                    str(f.get("project", "")),
                    int(f["fit"]) if str(f.get("fit", "")).strip().lstrip("-").isdigit() else None,
                    str(f.get("why", "")),
                    str(f.get("source", "")),
                    json.dumps(f, ensure_ascii=False, sort_keys=True),
                )
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO findings ({','.join(_COLS)}) "
                    f"VALUES ({','.join('?' * len(_COLS))});",
                    row,
                )
                appended += cur.rowcount  # 1 if inserted, 0 if dup
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
        return (appended, len(findings) - appended)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_source(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    return Path(arg).expanduser().read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sinks")
    sub = p.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("append", help="append findings to the sqlite sink")
    ap.add_argument("--db", default=None, help="db path, or '-' for default")
    ap.add_argument("--json", required=True, help="findings JSON list, or '-' for stdin")
    args = p.parse_args(argv)

    if args.cmd == "append":
        payload = json.loads(_read_source(args.json) or "[]")
        if not isinstance(payload, list):
            print("error: --json must be a JSON list of findings", file=sys.stderr)
            return 2
        db = None if (args.db in (None, "-")) else args.db
        sink = SqliteSink(db)
        appended, dupes = sink.append(payload)
        print(f"appended={appended} skipped_dupes={dupes} db={sink.db_path}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

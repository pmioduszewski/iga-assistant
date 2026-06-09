"""Thin CLI entrypoint for the Iga proactive engine.

WHY THIS EXISTS
---------------
Wave 1+2 built and froze the engine (``ledger``, ``governor``, ``schema``,
``triggers``, ``runtime``, ``dispatcher``, ``surfacer``). This module is the
**thinnest possible relay** on top of that frozen core so an operator (or a
``/gm``/``/back`` shell-out) can run one real scan tick from the command
line and see exactly what WOULD be dispatched.

HARD BOUNDARY (from SKILL.md § "Hard boundary")
-----------------------------------------------
*The engine decides. The entrypoint only relays.*

This file makes **zero** admission decisions. It does not dedup, does not
budget-gate, does not spawn anything. It:

  1. calls the frozen :func:`runtime.scan_tick` (which itself wires the real
     ledger claim + governor gate + real Todoist token + real MemPalace),
  2. converts the gated queue to WORKER_REQUESTs via the frozen
     :func:`dispatcher.build_dispatch`,
  3. prints them and (non-dry) writes the frozen JSON state file.

Deleting this file leaves the engine 100% intact — the inline path
(``scan_tick`` called in-session) still works with zero infrastructure.

ENV CONTRACT (honoured, not re-implemented — these are read by the frozen
core's ``default_db_path()`` / ``default_state_path()``)
--------------------------------------------------------
  * ``IGA_PROACTIVE_DB``      — ledger/governor sqlite db path
  * ``IGA_PROACTIVE_STATE``   — JSON state file path
  * ``IGA_PROACTIVE_RESEARCH=0`` — killswitch: detect nothing, queue nothing
  * ``IGA_PROACTIVE_SPAWN=0``  — detect + dedup-preview but DO NOT mutate the
    ledger and DO NOT write state (same effect as ``--dry-run``)
  * ``TODOIST_API_TOKEN`` / ``~/.config/todoist/token`` — Todoist auth; absent
    → the trigger layer yields nothing (graceful, exit 0)

DRY-RUN SEMANTICS
-----------------
``--dry-run`` (or ``IGA_PROACTIVE_SPAWN=0``) must mutate **nothing**: no
ledger row, no state file. Because ``scan_tick`` performs the real
``ledger.claim`` inside itself, a dry preview cannot call the real tick
against the real db. Instead it runs the tick against a **throwaway temp db**
(copy-free, fresh) so detection + condition + key-rendering + a *preview*
dedup decision all execute for real, while the production ledger is never
touched. The per-candidate "would queue / would skip (reason)" verdict is
computed by consulting the *production* ledger read-only (``should_skip``),
which never writes.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # package import
    from .ledger import Ledger, default_db_path
    from .governor import Governor
    from .runtime import scan_tick
    from . import dispatcher as disp
    from . import triggers as triggers_mod
except ImportError:  # flat import (engine/ on sys.path — repo house pattern)
    from ledger import Ledger, default_db_path  # type: ignore
    from governor import Governor  # type: ignore
    from runtime import scan_tick  # type: ignore
    import dispatcher as disp  # type: ignore
    import triggers as triggers_mod  # type: ignore


def _truthy_off(var: str) -> bool:
    """An env killswitch is 'engaged' when explicitly set to 0/false/off/no."""
    v = os.environ.get(var)
    if v is None:
        return False
    return v.strip().lower() in ("0", "false", "off", "no")


def _eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #
def _run_scan(args: argparse.Namespace) -> int:
    """Run one scan. Returns a process exit code (0 on every normal path,
    including zero candidates and a missing Todoist token — the engine is
    designed to degrade to 'no candidates', not to error)."""
    now = datetime.now(timezone.utc)

    # --dry-run OR IGA_PROACTIVE_SPAWN=0 → preview only, mutate nothing.
    dry = bool(args.dry_run) or _truthy_off("IGA_PROACTIVE_SPAWN")

    # IGA_PROACTIVE_RESEARCH=0 → killswitch: behave as a clean empty scan.
    if _truthy_off("IGA_PROACTIVE_RESEARCH"):
        return _emit_killswitched(args, dry)

    prod_db = (
        Path(args.db).expanduser() if args.db else default_db_path()
    )

    if dry:
        return _run_dry(args, now, prod_db)
    return _run_live(args, now, prod_db)


def _emit_killswitched(args: argparse.Namespace, dry: bool) -> int:
    """IGA_PROACTIVE_RESEARCH=0 — emit an explicit empty result, write no
    state, mutate no ledger. Exit 0 (a disabled engine is not an error)."""
    if args.json:
        print(
            json.dumps(
                {
                    "killswitch": "IGA_PROACTIVE_RESEARCH=0",
                    "dry_run": dry,
                    "queue": [],
                    "tick": None,
                    "state_path": None,
                },
                indent=2,
            )
        )
    else:
        _eprint("IGA_PROACTIVE_RESEARCH=0 — proactive engine disabled, nothing queued")
        print("[]")
    return 0


def _run_live(
    args: argparse.Namespace, now: datetime, prod_db: Path
) -> int:
    """Real tick: the frozen scan_tick performs the real ledger claim +
    governor gate against the production db; we only relay the result."""
    governor = Governor(prod_db)
    res = scan_tick(now=now, db_path=prod_db, governor=governor)

    state_path = (
        Path(args.state).expanduser()
        if args.state
        else disp.default_state_path()
    )
    requests, state = disp.build_dispatch(
        res,
        governor=governor,
        state_path=state_path,
        write_state=True,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "dry_run": False,
                    "tick": state["tick"],
                    "queue": requests,
                    "counts": state["counts"],
                    "governor": state["governor"],
                    "state_path": str(state_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(requests, indent=2, ensure_ascii=False))
        _eprint(
            f"[scan] discovered={res.discovered_jobs} fired={res.fired_candidates} "
            f"condition_skipped={res.condition_skipped} "
            f"claim_skipped={res.claim_skipped} "
            f"governor_denied={res.governor_denied} "
            f"queued={len(requests)} state={state_path}"
        )
        for e in res.errors:
            _eprint(f"[scan] error: {e}")
    return 0


def _run_dry(
    args: argparse.Namespace, now: datetime, prod_db: Path
) -> int:
    """Preview: run the real detection + condition + key rendering against a
    THROWAWAY temp db (so claim() can run for real without touching the
    production ledger), then annotate each fired candidate with the verdict
    it WOULD get against the *production* ledger (read-only should_skip).

    Mutates nothing in production: no ledger row, no state file.
    """
    with tempfile.TemporaryDirectory(prefix="iga-proactive-dry-") as td:
        scratch_db = Path(td) / "scratch.db"
        # scan_tick against the scratch db: real triggers, real condition
        # eval, real key rendering, real (throwaway) claim/governor — none
        # of which can affect production because the db is disposable.
        res = scan_tick(now=now, db_path=scratch_db)

    prod_ledger = Ledger(prod_db)
    would_queue: list[dict[str, Any]] = []
    would_skip: list[dict[str, Any]] = []
    for qc in res.queue:
        req = disp.to_worker_request(qc)
        # Read-only verdict against the REAL ledger (no write).
        if prod_ledger.should_skip(qc.idempotency_key):
            would_skip.append(
                {
                    "idempotency_key": qc.idempotency_key,
                    "job_id": qc.job.id,
                    "reason": "live ledger row exists (within cooldown / active)",
                }
            )
        else:
            would_queue.append(req)

    payload = {
        "dry_run": True,
        "tick": {
            "discovered_jobs": res.discovered_jobs,
            "fired_candidates": res.fired_candidates,
            "condition_skipped": res.condition_skipped,
            "claim_skipped": res.claim_skipped,
            "governor_denied": res.governor_denied,
            "queue_alert": res.queue_alert,
            "skipped_non_proactive": res.skipped_non_proactive,
            "errors": list(res.errors),
        },
        "would_queue": would_queue,
        "would_skip": would_skip,
        "state_path": None,
        "mutated": False,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(would_queue, indent=2, ensure_ascii=False))
        _eprint(
            f"[scan --dry-run] discovered={res.discovered_jobs} "
            f"fired={res.fired_candidates} would_queue={len(would_queue)} "
            f"would_skip={len(would_skip)} (NO ledger/state mutation)"
        )
        for s in would_skip:
            _eprint(
                f"[scan --dry-run] skip {s['idempotency_key']} "
                f"({s['job_id']}): {s['reason']}"
            )
        for e in res.errors:
            _eprint(f"[scan --dry-run] error: {e}")
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m engine",
        description=(
            "Thin CLI relay over the frozen Iga proactive engine. "
            "Runs ONE real scan tick (the engine decides; this only relays). "
            "Honours IGA_PROACTIVE_DB, IGA_PROACTIVE_STATE, "
            "IGA_PROACTIVE_RESEARCH=0 (killswitch), IGA_PROACTIVE_SPAWN=0 "
            "(detect-but-don't-mutate, == --dry-run). Missing Todoist token "
            "is graceful (yields nothing, exit 0)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m engine scan              # real tick: print "
            "WORKER_REQUESTs as JSON, write state\n"
            "  python -m engine scan --dry-run    # preview only, mutate "
            "nothing (no ledger row, no state file)\n"
            "  python -m engine scan --json       # full machine-readable "
            "result (tick stats + queue + state path)\n\n"
            "run from skills/iga-proactive/ (the repo's flat-import house "
            "style):\n"
            "  cd <iga-assistant>/skills/iga-proactive && "
            "PYTHONPATH=engine python -m engine scan\n"
        ),
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sp = sub.add_parser(
        "scan",
        help="run one scan tick (the only command)",
        description=(
            "Run ONE real scan_tick: discover proactive jobs, fire triggers, "
            "dedup via the real ledger, gate via the real governor, print the "
            "resulting WORKER_REQUEST list as JSON to stdout, and (unless "
            "--dry-run) write the JSON state file. Exit 0 even with zero "
            "candidates (prints [])."
        ),
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "preview only: run the same detection + dedup DECISIONS but DO "
            "NOT mutate the ledger and DO NOT write state. Prints what WOULD "
            "be queued and why each candidate would be skipped."
        ),
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help=(
            "emit one machine-readable JSON object: tick stats + queue + "
            "state path (instead of the bare WORKER_REQUEST list)."
        ),
    )
    sp.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=(
            "override the ledger/governor sqlite db (else $IGA_PROACTIVE_DB "
            "or ~/Iga/state/proactive.db)."
        ),
    )
    sp.add_argument(
        "--state",
        metavar="PATH",
        default=None,
        help=(
            "override the JSON state-file path (else $IGA_PROACTIVE_STATE or "
            "~/Iga/scratch/proactive-state.json)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "scan":
        parser.print_help()
        return 0
    return _run_scan(args)


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())

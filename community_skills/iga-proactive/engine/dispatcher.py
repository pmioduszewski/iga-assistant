"""Dispatcher — queued candidates → WORKER_REQUEST records + engine state file.

WHY THIS EXISTS
---------------
``runtime.scan_tick`` produces a list of :class:`~engine.runtime.QueuedCandidate`
that have ALREADY passed the ledger claim + governor gate. This module is the
seam between the engine and whatever entrypoint actually runs the work.

INLINE MODE (the only mode in Wave 2; daemon/menu-bar is Wave 3)
----------------------------------------------------------------
The engine does **NOT** spawn subagents/LLMs itself. It emits structured
``WORKER_REQUEST`` dicts. The calling Claude Code session (e.g. ``/gm``,
``/back``) reads them and dispatches the workers via its own Agent tool.
This keeps the hard boundary from ``SKILL.md``: *the engine decides; the
entrypoint only relays*. Deleting every future entrypoint must still leave
this inline path working with zero external infrastructure.

THE JSON STATE FILE (contract for the future menu-bar app)
----------------------------------------------------------
``build_dispatch`` also writes a single JSON state file that the future
menu-bar app polls **read-only**. It is the stable engine→entrypoint contract.
Default path: ``$IGA_PROACTIVE_STATE`` or ``~/Gaia/scratch/proactive-state.json``.

  WHY ``scratch/``: ``.gitignore`` ignores ``scratch/`` and ``*.db`` but does
  NOT ignore ``*.json`` repo-wide. Writing the state file under ``scratch/``
  keeps ``git status`` clean by construction (verified). ``$IGA_PROACTIVE_STATE``
  lets the menu-bar app point elsewhere without code changes.

Schema (v1) — see ``STATE_SCHEMA_VERSION`` and the module docstring of
``surfacer.py`` which refreshes the same file:

```
{
  "schema_version": 1,
  "generated_at": "<ISO8601 UTC>",
  "tick": {
    "discovered_jobs": int,
    "fired_candidates": int,
    "condition_skipped": int,
    "claim_skipped": int,
    "governor_denied": int,
    "queue_alert": bool,
    "skipped_non_proactive": int,
    "errors": [str, ...]
  },
  "queue": [WORKER_REQUEST, ...],   # what is about to be dispatched
  "counts": { "queued": int, "running": int, "done": int },
  "governor": { ...Governor.stats()... }
}
```

A ``WORKER_REQUEST`` is:

```
{
  "job_id": str,
  "idempotency_key": str,          # the ledger holds a live 'claimed' row
  "trigger_kind": str,
  "action": str,                   # raw action expr from the job
  "action_name": str,              # parsed ("spawn_worker")
  "prompt_path": str | null,       # resolved abs path if action carries one
  "model": str,
  "est_tokens": int,
  "deliver": str,
  "context": { ... candidate render namespace ... }
}
```

Stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # package import
    from .runtime import TickResult, QueuedCandidate
    from .governor import Governor
except ImportError:  # flat import (engine/ on sys.path — house pattern)
    from runtime import TickResult, QueuedCandidate  # type: ignore
    from governor import Governor  # type: ignore

LOG = logging.getLogger("iga_proactive.dispatcher")

STATE_SCHEMA_VERSION = 1

_DEFAULT_STATE_PATH = "~/Gaia/scratch/proactive-state.json"


def default_state_path() -> Path:
    """``$IGA_PROACTIVE_STATE`` if set, else
    ``~/Gaia/scratch/proactive-state.json`` (scratch/ is gitignored)."""
    env = os.environ.get("IGA_PROACTIVE_STATE")
    if env:
        return Path(env).expanduser()
    return Path(_DEFAULT_STATE_PATH).expanduser()


# --------------------------------------------------------------------------- #
# action-arg helpers — extract the prompt path from spawn_worker(prompt: x.md)
# --------------------------------------------------------------------------- #
_PROMPT_ARG_RE = re.compile(r"prompt\s*:\s*([^,\)]+)")


def extract_prompt_path(action_args: str, *, base: Path | None = None) -> str | None:
    """Pull ``prompt: <path>`` out of an action's raw args and resolve it.

    Relative paths resolve against ``base`` (the source file's directory) when
    given, else returned expanded-but-relative. Returns ``None`` if the action
    carries no prompt (not every action spawns a prompted worker).
    """
    if not action_args:
        return None
    m = _PROMPT_ARG_RE.search(action_args)
    if not m:
        return None
    raw = m.group(1).strip().strip("'\"")
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute() and base is not None:
        p = (base / p).resolve()
    return str(p)


# --------------------------------------------------------------------------- #
# WORKER_REQUEST construction
# --------------------------------------------------------------------------- #
def to_worker_request(
    qc: QueuedCandidate,
    *,
    prompt_base: Path | None = None,
) -> dict[str, Any]:
    """Turn a gated :class:`QueuedCandidate` into a WORKER_REQUEST dict.

    The candidate has already won its ledger claim and passed the governor —
    this is a pure data transform, no admission logic here.
    """
    job = qc.job
    return {
        "job_id": job.id,
        "idempotency_key": qc.idempotency_key,
        "trigger_kind": qc.candidate.trigger_kind,
        "action": job.action.raw,
        "action_name": job.action.name,
        "prompt_path": extract_prompt_path(job.action.args, base=prompt_base),
        "model": qc.model,
        "est_tokens": qc.est_tokens,
        "deliver": job.deliver,
        "context": qc.candidate.render_context(),
    }


# --------------------------------------------------------------------------- #
# State file
# --------------------------------------------------------------------------- #
def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically (tmp + os.replace) so a polling reader never
    sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def build_state(
    result: TickResult,
    requests: list[dict[str, Any]],
    *,
    governor: Governor | None = None,
    running: int = 0,
    done: int = 0,
) -> dict[str, Any]:
    """Assemble the v1 state document (also reused by surfacer.py)."""
    gov_stats: dict[str, Any] = {}
    if governor is not None:
        try:
            gov_stats = governor.stats()
        except Exception as exc:  # noqa: BLE001 — stats must never break dispatch
            LOG.warning("governor.stats() failed: %s", exc)
            gov_stats = {"error": str(exc)}
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tick": {
            "discovered_jobs": result.discovered_jobs,
            "fired_candidates": result.fired_candidates,
            "condition_skipped": result.condition_skipped,
            "claim_skipped": result.claim_skipped,
            "governor_denied": result.governor_denied,
            "queue_alert": result.queue_alert,
            "skipped_non_proactive": result.skipped_non_proactive,
            "errors": list(result.errors),
        },
        "queue": requests,
        "counts": {
            "queued": len(requests),
            "running": running,
            "done": done,
        },
        "governor": gov_stats,
    }


def build_dispatch(
    result: TickResult,
    *,
    governor: Governor | None = None,
    prompt_base: Path | None = None,
    state_path: Path | str | None = None,
    write_state: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convert a TickResult → (worker_requests, state_doc) and persist state.

    Returns the list the inline entrypoint dispatches AND the state document
    written to disk. ``write_state=False`` skips the file (used by tests that
    only assert on the request shape).
    """
    requests = [
        to_worker_request(qc, prompt_base=prompt_base) for qc in result.queue
    ]
    state = build_state(result, requests, governor=governor)
    if write_state:
        path = (
            Path(state_path).expanduser()
            if state_path
            else default_state_path()
        )
        _atomic_write_json(path, state)
        LOG.info("Wrote engine state → %s (%d queued)", path, len(requests))
    return requests, state


def read_state(state_path: Path | str | None = None) -> dict[str, Any]:
    """Read the state file back (helper for entrypoints / the menu-bar app)."""
    path = (
        Path(state_path).expanduser() if state_path else default_state_path()
    )
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

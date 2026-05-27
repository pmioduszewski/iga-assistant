"""Surfacer — turn completed research into a /gm + /back surface payload.

WHY THIS EXISTS
---------------
After workers run, their outputs (research drawers) need to reach the user at
the next natural touchpoint (``/gm``, ``/back``) — not via a push/interrupt.
This module produces that surface payload and refreshes the same JSON state
file ``dispatcher.py`` writes, so the future menu-bar app sees fresh "done"
counts without the engine making any MCP call itself.

HARD BOUNDARY
-------------
**No MCP / network here.** The surfacer operates on:
  * the frozen Wave 1 ledger (read-only — done/failed rows + output_ref), and
  * an injected ``output_resolver`` callable that maps an ``output_ref`` to a
    small ``{title, tldr}`` dict.
The resolver is the entry point: in production an entrypoint passes a closure that
reads a drawer via MemPalace; in tests it's a dict lookup. The surfacer never
imports MemPalace.

SURFACE PAYLOAD
---------------
A compact, capped list of ``📑 <project>: <TLDR>`` lines for inline render:

```
{
  "lines": ["📑 acme: Pricing undercuts us 12% on mid-tier", ...],
  "shown": int,
  "total": int,
  "overflow": "+N more" | null
}
```

Caps default to :data:`DEFAULT_SURFACE_CAP`. Over the cap → an ``"+N more"``
overflow marker (never silently drops the count).

Stdlib only.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # package import
    from .ledger import Ledger, default_db_path
    from .dispatcher import default_state_path, _atomic_write_json, STATE_SCHEMA_VERSION
except ImportError:  # flat import (engine/ on sys.path — house pattern)
    from ledger import Ledger, default_db_path  # type: ignore
    from dispatcher import (  # type: ignore
        default_state_path,
        _atomic_write_json,
        STATE_SCHEMA_VERSION,
    )

LOG = logging.getLogger("iga_proactive.surfacer")

DEFAULT_SURFACE_CAP = 5

# An output_resolver maps output_ref -> {"title": str, "tldr": str} (or None
# if the ref no longer resolves — surfaced as a degraded line, never crashes).
OutputResolver = Callable[[str], "dict[str, str] | None"]


# --------------------------------------------------------------------------- #
# Ledger reads (no extra Ledger API surface — plain read-only SQL on its db)
# --------------------------------------------------------------------------- #
def _completed_rows(db_path: Path) -> list[dict[str, Any]]:
    """Read done rows with a non-null output_ref, newest first.

    Read-only; opens its own short-lived connection (same pattern the frozen
    Ledger uses). Does NOT mutate the ledger.
    """
    if not Path(db_path).is_file():
        return []
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT idempotency_key, job_id, last_run_ts, output_ref "
            "FROM job_runs "
            "WHERE status = 'done' AND output_ref IS NOT NULL "
            "ORDER BY last_run_ts DESC;"
        ).fetchall()
    except sqlite3.Error as exc:
        LOG.warning("surfacer ledger read failed: %s", exc)
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _project_from_key(idempotency_key: str) -> str:
    """Best-effort project label for the ``📑 <project>:`` prefix.

    Keys conventionally look like ``research::<task_id>::<due>`` or
    ``<project>::...``. We take the first ``::``-segment if it isn't the
    generic ``research`` literal, else fall back to ``research``.
    """
    head = idempotency_key.split("::", 1)[0].strip()
    return head or "research"


# --------------------------------------------------------------------------- #
# Surface payload
# --------------------------------------------------------------------------- #
def build_surface(
    *,
    db_path: Path | str | None = None,
    output_resolver: OutputResolver,
    cap: int = DEFAULT_SURFACE_CAP,
) -> dict[str, Any]:
    """Produce the capped ``/gm`` + ``/back`` surface payload.

    Pure: ledger read + injected resolver only. A ref the resolver can't
    resolve becomes a degraded ``(no summary)`` line — we never drop the
    fact that something completed, and we never raise.
    """
    path = Path(db_path).expanduser() if db_path else default_db_path()
    rows = _completed_rows(path)

    lines: list[str] = []
    for row in rows:
        ref = row["output_ref"]
        project = _project_from_key(row["idempotency_key"])
        try:
            resolved = output_resolver(ref)
        except Exception as exc:  # noqa: BLE001 — resolver must not break surface
            LOG.warning("output_resolver raised for %r: %s", ref, exc)
            resolved = None
        if resolved and resolved.get("tldr"):
            proj = resolved.get("title_project") or project
            lines.append(f"📑 {proj}: {resolved['tldr']}")
        else:
            lines.append(f"📑 {project}: (no summary — ref {ref})")

    total = len(lines)
    shown = lines[:cap] if cap >= 0 else lines
    overflow = None
    if total > len(shown):
        overflow = f"+{total - len(shown)} more"
    return {
        "lines": shown,
        "shown": len(shown),
        "total": total,
        "overflow": overflow,
    }


# --------------------------------------------------------------------------- #
# State-file refresh (same v1 schema dispatcher.py writes)
# --------------------------------------------------------------------------- #
def _counts(db_path: Path) -> dict[str, int]:
    """Live status tally straight off the ledger (read-only)."""
    out = {"queued": 0, "running": 0, "done": 0}
    if not Path(db_path).is_file():
        return out
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM job_runs GROUP BY status;"
        ):
            st = r["status"]
            if st == "claimed":
                out["queued"] += r["n"]
            elif st == "running":
                out["running"] += r["n"]
            elif st == "done":
                out["done"] += r["n"]
    except sqlite3.Error as exc:
        LOG.warning("surfacer counts read failed: %s", exc)
    finally:
        conn.close()
    return out


def refresh_state(
    surface: dict[str, Any],
    *,
    db_path: Path | str | None = None,
    state_path: Path | str | None = None,
    governor_stats: dict[str, Any] | None = None,
) -> Path:
    """Refresh the JSON state file with the latest surface + ledger counts.

    Used by ``/gm``/``/back`` after surfacing so the menu-bar app's poll sees
    fresh done counts. Does NOT recompute a tick — it overlays the surface and
    live counts onto the v1 schema. No MCP calls.
    """
    path = (
        Path(state_path).expanduser() if state_path else default_state_path()
    )
    dbp = Path(db_path).expanduser() if db_path else default_db_path()
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "surface": surface,
        "counts": _counts(dbp),
        "governor": governor_stats or {},
    }
    _atomic_write_json(path, payload)
    LOG.info("Refreshed engine state (surface) → %s", path)
    return path

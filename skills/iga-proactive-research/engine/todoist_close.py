"""Close the Todoist loop after a research worker files its drawer.

A finished research task must stop looking pending: the ``iga-research``
label comes off (other labels kept) and the task gets ONE comment with the
TL;DR and the drawer id. Without this the task stays labelled, and every
scan after the ledger cooldown re-queues it.

Called by ``dispatch_runner.py`` after each headless worker exits (headless
workers have no Bash and no Todoist tool). Every step is idempotent: the
label is only rewritten when present, and the comment is skipped when one
already names the drawer, so a worker that closed the loop itself (inline
mode) is never doubled.

Stdlib only. HTTP and the palace module are injectable for tests.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger("iga_proactive_research.todoist_close")

API_BASE = "https://api.todoist.com/api/v1"
RESEARCH_LABEL = "iga-research"
RESEARCH_ROOM = "research"
TOKEN_FILE = "~/.config/todoist/token"

# (method, path, json_body | None, token) -> parsed JSON response
Http = Callable[[str, str, "dict[str, Any] | None", str], Any]


class CloseError(RuntimeError):
    """Todoist or palace call failed; the caller logs it and moves on."""


def load_token() -> str | None:
    tok = os.environ.get("TODOIST_API_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    p = Path(TOKEN_FILE).expanduser()
    if p.is_file():
        return p.read_text(encoding="utf-8").strip() or None
    return None


def _default_http(method: str, path: str, body: dict[str, Any] | None, token: str) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CloseError(f"{method} {path}: {exc}") from exc
    return json.loads(raw) if raw.strip() else {}


# --------------------------------------------------------------------------- #
# palace: find the research drawer a worker filed for this task
# --------------------------------------------------------------------------- #
def parse_tldr(content: str) -> str:
    for line in (content or "").splitlines():
        if line.strip().upper().startswith("TLDR:"):
            return line.split(":", 1)[1].strip()
    return ""


def find_research_drawer(mempalace_mod: Any, source_file: str) -> dict[str, str] | None:
    """Return ``{"drawer_id", "tldr"}`` for the newest research drawer whose
    ``source_file`` matches, or None. Chunked drawers resolve to their
    ``parent_drawer_id`` and the TL;DR is read from chunk 0."""
    try:
        col = mempalace_mod._get_collection()
        if not col:
            raise CloseError("no palace collection")
        res = col.get(
            where={"$and": [{"source_file": source_file}, {"room": RESEARCH_ROOM}]},
            include=["documents", "metadatas"],
        )
    except CloseError:
        raise
    except Exception as exc:  # noqa: BLE001, normalised
        raise CloseError(f"palace query failed: {exc}") from exc

    rows = list(zip(res.get("ids") or [], res.get("documents") or [], res.get("metadatas") or []))
    if not rows:
        return None
    # Newest filing first; within it, chunk 0 carries the AAAK header + TLDR.
    rows.sort(
        key=lambda r: (str((r[2] or {}).get("filed_at", "")), -int((r[2] or {}).get("chunk_index", 0) or 0)),
        reverse=True,
    )
    did, doc, meta = rows[0]
    meta = meta or {}
    return {"drawer_id": str(meta.get("parent_drawer_id") or did), "tldr": parse_tldr(doc or "")}


# --------------------------------------------------------------------------- #
# Todoist: drop the label, post one comment
# --------------------------------------------------------------------------- #
def comment_text(tldr: str, drawer_id: str) -> str:
    return f"[Iga prepared] {tldr or '(no TL;DR in drawer)'}\nDrawer: {drawer_id}"


def close_task(
    task_id: str,
    *,
    drawer_id: str,
    tldr: str,
    token: str,
    label: str = RESEARCH_LABEL,
    http: Http | None = None,
) -> dict[str, Any]:
    """Remove ``label`` from the task (keeping the rest) and post the
    TL;DR comment unless one already references ``drawer_id``."""
    call = http or _default_http
    tid = urllib.parse.quote(str(task_id), safe="")

    task = call("GET", f"/tasks/{tid}", None, token) or {}
    labels = list(task.get("labels") or [])
    label_removed = False
    if label in labels:
        call("POST", f"/tasks/{tid}", {"labels": [x for x in labels if x != label]}, token)
        label_removed = True

    marker = f"Drawer: {drawer_id}"
    comments = call("GET", f"/comments?task_id={tid}", None, token) or {}
    existing = comments.get("results", []) if isinstance(comments, dict) else comments
    if any(marker in (c.get("content") or "") for c in existing or []):
        comment = "exists"
    else:
        call("POST", "/comments", {"task_id": str(task_id), "content": comment_text(tldr, drawer_id)}, token)
        comment = "posted"
    return {"task_id": str(task_id), "drawer_id": drawer_id, "label_removed": label_removed, "comment": comment}


def close_for_request(
    entry: dict[str, Any],
    *,
    mempalace_mod: Any,
    token: str | None,
    http: Http | None = None,
) -> dict[str, Any]:
    """Post-step for one WORKER_REQUEST. Never raises: returns a status dict
    the dispatcher logs. Only Todoist-triggered requests are touched, and
    only once a research drawer for the task exists (no drawer means the
    worker failed, so the label stays and the task is retried)."""
    if entry.get("trigger_kind") != "todoist":
        return {"skipped": "not a todoist request"}
    task_id = str((entry.get("context") or {}).get("task.id") or "")
    if not task_id:
        return {"skipped": "no task.id in request context"}
    try:
        drawer = find_research_drawer(mempalace_mod, f"todoist:{task_id}")
        if drawer is None:
            return {"skipped": "no research drawer filed for task", "task_id": task_id}
        if not token:
            return {"error": "no Todoist token", "task_id": task_id}
        return close_task(task_id, drawer_id=drawer["drawer_id"], tldr=drawer["tldr"], token=token, http=http)
    except (CloseError, urllib.error.HTTPError, ValueError) as exc:
        LOG.warning("todoist close failed for %s: %s", task_id, exc)
        return {"error": str(exc), "task_id": task_id}

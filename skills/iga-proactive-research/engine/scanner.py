#!/usr/bin/env python3
"""Iga Proactive Research scanner (v2).

Layer 1 of the proactive-research architecture (see
``skills/iga-proactive-research/SKILL.md``). The scanner detects research
candidates from Todoist (``iga-research`` label) and MemPalace
``research-queue`` flag drawers, dedupes against existing research
drawers, writes a work queue, and — depending on ``IGA_RUN_MODE`` —
either emits ``WORKER_REQUEST`` JSON to stdout for the calling Claude
Code session to dispatch (inline mode, default) or directly spawns
``claude --bare`` subprocesses (daemon mode).

Calendar was deliberately dropped in v2 — Todoist due dates already
carry the temporal signal. Revisit in a future version if signal
coverage proves insufficient.

Exit codes:
    0 success
    1 config error (missing Todoist token, missing worker prompt)
    2 MemPalace error
    3 Todoist error
    4 invalid IGA_RUN_MODE

Environment variables:
    IGA_PROACTIVE_RESEARCH=0  - fully disable the scanner (no-op exit 0)
    IGA_PROACTIVE_SPAWN=0     - detect + write queue but emit no worker
                                requests (inline) / spawn no subprocesses
                                (daemon)
    IGA_RUN_MODE              - "inline" (default) or "daemon"
    IGA_MAX_SPAWN_PER_TICK    - override the default spawn cap (3)
    TODOIST_API_TOKEN         - Todoist REST API v2 token. Fallback:
                                ``~/.config/todoist/token`` (single-line file).
    IGA_RESEARCH_QUEUE_PATH   - optional override for the queue file
                                (default ``~/Gaia/scratch/iga-research-queue.json``)
    IGA_RESEARCH_DRY_RUN=1    - same as IGA_PROACTIVE_SPAWN=0
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import urllib.error
import urllib.request

LOG = logging.getLogger("iga_research_scanner")

# --- Config (mirrors skills/iga-proactive-research/SKILL.md > Config) ----

LOOKAHEAD_DAYS = 7
DEDUP_WINDOW_HOURS = 48
MAX_SPAWN_PER_TICK = 3
QUEUE_ALERT_THRESHOLD = 10
TODOIST_LABEL = "iga-research"
MEMPALACE_RESEARCH_QUEUE_ROOM = "research-queue"
DEEP_KEYWORDS = (
    "trademark",
    "legal",
    "security incident",
    "competitive recon",
    "finance forecast",
    "contract review",
)
DEFAULT_QUEUE_PATH = "~/Gaia/scratch/iga-research-queue.json"
WORKER_PROMPT_PATH = "~/Gaia/skills/iga-proactive-research/engine/worker.prompt.md"
VALID_RUN_MODES = ("inline", "daemon")
ALLOWED_SOURCES = ("todoist", "mempalace")

# Regex stripping emoji + punctuation for stable hashing.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


# --- Public data model ---------------------------------------------------


@dataclasses.dataclass
class Candidate:
    """A research candidate, pre-dedup.

    ``source`` is restricted to values in :data:`ALLOWED_SOURCES`.
    """

    topic_hash: str
    source: str  # "todoist" | "mempalace"
    source_id: str
    title: str
    context: str
    target_date: str  # YYYY-MM-DD
    depth: str  # "shallow" | "deep"
    spawned_at: str | None = None
    completed_at: str | None = None

    def to_queue_entry(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# --- Hashing & normalization --------------------------------------------


def _is_emoji(ch: str) -> bool:
    if unicodedata.category(ch).startswith("S"):
        return True
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x1F000 <= cp <= 0x1F2FF
    )


def normalize_title(title: str) -> str:
    """Lowercase, strip emoji + punctuation, collapse whitespace.

    Diacritics are preserved on purpose: ``Łukasz`` and ``Lukasz`` are
    different people.
    """
    if not title:
        return ""
    stripped = "".join(c for c in title if not _is_emoji(c))
    stripped = _PUNCT_RE.sub(" ", stripped)
    stripped = stripped.lower()
    return " ".join(stripped.split())


def topic_hash(title: str, target_date: str) -> str:
    """Deterministic 16-char SHA1 prefix over normalized title + date."""
    key = f"{normalize_title(title)}|{target_date}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def classify_depth(title: str, context: str) -> str:
    haystack = f"{title}\n{context}".lower()
    for kw in DEEP_KEYWORDS:
        if kw in haystack:
            return "deep"
    return "shallow"


# --- Todoist source -----------------------------------------------------


def _load_todoist_token() -> str | None:
    tok = os.environ.get("TODOIST_API_TOKEN")
    if tok:
        return tok.strip()
    config_path = Path("~/.config/todoist/token").expanduser()
    if config_path.is_file():
        return config_path.read_text(encoding="utf-8").strip()
    return None


def fetch_todoist_candidates(token: str, *, today: datetime) -> list[Candidate]:
    """Return open tasks with the ``iga-research`` label due within the window."""
    cutoff = (today + timedelta(days=LOOKAHEAD_DAYS)).date()
    req = urllib.request.Request(
        f"https://api.todoist.com/api/v1/tasks?label={TODOIST_LABEL}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"Todoist API call failed: {exc}") from exc

    tasks = data.get("results", []) if isinstance(data, dict) else data

    out: list[Candidate] = []
    for task in tasks:
        due = task.get("due") or {}
        deadline = task.get("deadline") or {}
        target = due.get("date") or deadline.get("date")
        if not target:
            continue
        try:
            target_dt = datetime.strptime(target[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if target_dt > cutoff or target_dt < today.date():
            continue
        title = task.get("content", "").strip()
        if not title:
            continue
        ctx = (task.get("description") or "")[:600]
        depth = classify_depth(title, ctx)
        out.append(
            Candidate(
                topic_hash=topic_hash(title, target),
                source="todoist",
                source_id=str(task["id"]),
                title=title,
                context=ctx,
                target_date=target[:10],
                depth=depth,
            )
        )
    return out


# --- MemPalace source ---------------------------------------------------


def _import_mempalace():
    """Import the mempalace package, returning the mcp_server module."""
    from mempalace import mcp_server  # type: ignore

    return mcp_server


def fetch_mempalace_flag_candidates(mempalace_mod, *, today: datetime) -> list[Candidate]:
    """Return candidates from MemPalace ``research-queue`` drawers."""
    try:
        result = mempalace_mod.tool_list_drawers(
            room=MEMPALACE_RESEARCH_QUEUE_ROOM, limit=50
        )
    except Exception as exc:  # noqa: BLE001 - surface as MemPalace error
        raise RuntimeError(f"MemPalace list_drawers failed: {exc}") from exc

    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"MemPalace error: {result['error']}")

    drawers = result.get("drawers", []) if isinstance(result, dict) else []
    out: list[Candidate] = []
    for d in drawers:
        meta = d.get("metadata") or {}
        if str(meta.get("triggered", "false")).lower() == "true":
            continue
        title = (meta.get("title") or d.get("content", "")[:80]).strip()
        if not title:
            continue
        target = meta.get("target_date") or today.date().isoformat()
        ctx = (d.get("content", "") or "")[:600]
        depth = classify_depth(title, ctx)
        out.append(
            Candidate(
                topic_hash=topic_hash(title, target),
                source="mempalace",
                source_id=d.get("id", ""),
                title=title,
                context=ctx,
                target_date=target,
                depth=depth,
            )
        )
    return out


def mark_flag_triggered(mempalace_mod, drawer_id: str) -> None:
    """Best-effort: flip ``triggered: true`` on a research-queue drawer."""
    try:
        mempalace_mod.tool_update_drawer(
            drawer_id=drawer_id, content="[triggered]"
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not mark drawer %s as triggered: %s", drawer_id, exc)


# --- Dedup --------------------------------------------------------------


def is_duplicate(
    mempalace_mod,
    candidate: Candidate,
    *,
    now: datetime,
    dedup_window_hours: int = DEDUP_WINDOW_HOURS,
) -> bool:
    """Return True if a recent research drawer for this topic_hash exists."""
    try:
        result = mempalace_mod.tool_list_drawers(room="research", limit=200)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"MemPalace dedup query failed: {exc}") from exc

    drawers = result.get("drawers", []) if isinstance(result, dict) else []
    needle = f"RESEARCH:{candidate.topic_hash}"
    cutoff = now - timedelta(hours=dedup_window_hours)
    for d in drawers:
        content = d.get("content", "") or ""
        if not content.startswith(needle):
            continue
        ts_raw = (d.get("metadata") or {}).get("last_updated") or (
            d.get("metadata") or {}
        ).get("created_at")
        if not ts_raw:
            return True  # documented gap: name-only dedup
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > cutoff:
            return True
    return False


# --- Queue file ---------------------------------------------------------


def queue_path() -> Path:
    p = os.environ.get("IGA_RESEARCH_QUEUE_PATH", DEFAULT_QUEUE_PATH)
    return Path(p).expanduser()


def write_queue(entries: Iterable[Candidate], path: Path | None = None) -> Path:
    path = path or queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [c.to_queue_entry() if isinstance(c, Candidate) else c for c in entries]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_queue(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or queue_path()
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# --- Worker dispatch ----------------------------------------------------


def _worker_request(candidate: Candidate) -> dict[str, Any]:
    """Build the WORKER_REQUEST dict emitted in inline mode."""
    return {
        "topic_hash": candidate.topic_hash,
        "title": candidate.title,
        "context": candidate.context,
        "target_date": candidate.target_date,
        "depth": candidate.depth,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "worker_prompt_path": WORKER_PROMPT_PATH,
    }


def spawn_worker(
    candidate: Candidate,
    *,
    prompt_path: Path,
    runner: Any = subprocess.run,
) -> int:
    """Spawn one ``claude --bare`` headless worker (daemon mode)."""
    prompt_text = prompt_path.read_text(encoding="utf-8")
    payload = json.dumps(candidate.to_queue_entry())
    # Both depths use the 1M-context Opus model. Shallow research still
    # routinely blows past 200k due to WebFetch+MemPalace combined output;
    # 1M context eliminates autocompact thrash. Cost trade-off accepted.
    model = "claude-opus-4-7[1m]"
    cmd = [
        "claude",
        "-p",
        prompt_text,
        "--model",
        model,
        "--tools",
        "WebSearch,WebFetch,Bash,Read,Write,mcp__IgaMemory__mempalace_status,mcp__IgaMemory__mempalace_search,mcp__IgaMemory__mempalace_list_drawers,mcp__IgaMemory__mempalace_add_drawer,mcp__IgaMemory__mempalace_get_drawer",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--session-id",
        str(uuid.uuid4()),
    ]
    LOG.info("Spawning worker for %s (depth=%s, model=%s)", candidate.title, candidate.depth, model)
    result = runner(cmd, input=payload, text=True, capture_output=True, timeout=60 * 35)
    rc = getattr(result, "returncode", 0)
    stderr_tail = (getattr(result, "stderr", "") or "")[-1500:]
    stdout_tail = (getattr(result, "stdout", "") or "")[-2500:]
    if rc != 0:
        LOG.warning(
            "Worker exited rc=%s for %s\nstdout: %s\nstderr: %s",
            rc, candidate.title, stdout_tail, stderr_tail,
        )
    else:
        LOG.info(
            "Worker exited rc=0 for %s\nstdout: %s\nstderr: %s",
            candidate.title, stdout_tail, stderr_tail,
        )
    return rc


# --- Meter (cost guardrail) --------------------------------------------


def bump_invocation_meter(mempalace_mod, *, when: datetime, count: int) -> None:
    if count <= 0:
        return
    year, week, _ = when.isocalendar()
    body = (
        f"ITER:{when.isoformat()}|count:{count}|week:{year}-W{week:02d}"
    )
    try:
        mempalace_mod.tool_add_drawer(
            wing="iga/tooling",
            room="iga-research-meter",
            content=body,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not update invocation meter: %s", exc)


# --- Orchestration ------------------------------------------------------


def killswitch_active() -> bool:
    return os.environ.get("IGA_PROACTIVE_RESEARCH", "1") == "0"


def spawn_disabled() -> bool:
    return (
        os.environ.get("IGA_PROACTIVE_SPAWN", "1") == "0"
        or os.environ.get("IGA_RESEARCH_DRY_RUN", "0") == "1"
    )


def _max_spawn_per_tick() -> int:
    raw = os.environ.get("IGA_MAX_SPAWN_PER_TICK")
    if raw is None:
        return MAX_SPAWN_PER_TICK
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        LOG.warning("Invalid IGA_MAX_SPAWN_PER_TICK=%r, falling back to %d", raw, MAX_SPAWN_PER_TICK)
        return MAX_SPAWN_PER_TICK


def _run_mode() -> str:
    return os.environ.get("IGA_RUN_MODE", "inline").strip().lower()


def run(
    *,
    now: datetime | None = None,
    mempalace_mod: Any | None = None,
    worker_runner: Any = subprocess.run,
    stdout: Any = None,
) -> int:
    """Main entrypoint. Returns shell exit code."""
    logging.basicConfig(
        level=os.environ.get("IGA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    if killswitch_active():
        LOG.info("IGA_PROACTIVE_RESEARCH=0, exiting.")
        return 0

    mode = _run_mode()
    if mode not in VALID_RUN_MODES:
        LOG.error("Invalid IGA_RUN_MODE=%r (expected one of %s)", mode, VALID_RUN_MODES)
        return 4

    out_stream = stdout if stdout is not None else sys.stdout

    now = now or datetime.now(timezone.utc)
    todoist_token = _load_todoist_token()
    if not todoist_token:
        LOG.error(
            "No Todoist token found. Set TODOIST_API_TOKEN in env or write the "
            "token to ~/.config/todoist/token (single line)."
        )
        return 1

    if mempalace_mod is None:
        try:
            mempalace_mod = _import_mempalace()
        except Exception as exc:  # noqa: BLE001
            LOG.error("Could not import mempalace: %s", exc)
            return 2

    # --- gather ---
    try:
        todoist_cands = fetch_todoist_candidates(todoist_token, today=now)
    except RuntimeError as exc:
        LOG.warning("Todoist fetch failed (transient?): %s — continuing with MemPalace candidates only", exc)
        todoist_cands = []
    try:
        mempalace_cands = fetch_mempalace_flag_candidates(mempalace_mod, today=now)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 2

    all_cands = [*todoist_cands, *mempalace_cands]
    LOG.info(
        "Gathered candidates: todoist=%d mempalace=%d",
        len(todoist_cands),
        len(mempalace_cands),
    )

    # --- dedup ---
    fresh: list[Candidate] = []
    seen_hashes: set[str] = set()
    for c in all_cands:
        if c.topic_hash in seen_hashes:
            continue
        seen_hashes.add(c.topic_hash)
        try:
            if is_duplicate(mempalace_mod, c, now=now):
                LOG.info("Dedup skip: %s", c.title)
                continue
        except RuntimeError as exc:
            LOG.error("%s", exc)
            return 2
        fresh.append(c)

    # --- write queue (always, before any spawn logic) ---
    qpath = write_queue(fresh)
    LOG.info("Wrote queue (%d entries) -> %s", len(fresh), qpath)

    cap = _max_spawn_per_tick()

    if len(fresh) > QUEUE_ALERT_THRESHOLD:
        LOG.warning(
            "Queue length %d exceeds alert threshold %d — pausing spawns.",
            len(fresh),
            QUEUE_ALERT_THRESHOLD,
        )
        if mode == "inline":
            out_stream.write(json.dumps([]) + "\n")
            out_stream.flush()
        return 0

    if spawn_disabled():
        LOG.info("Spawn disabled (IGA_PROACTIVE_SPAWN=0); detection-only run.")
        if mode == "inline":
            out_stream.write(json.dumps([]) + "\n")
            out_stream.flush()
        return 0

    to_dispatch = fresh[:cap]

    if mode == "inline":
        # Emit WORKER_REQUEST JSON array to stdout. The calling Claude
        # Code session reads it and dispatches Agent tool calls.
        requests = [_worker_request(c) for c in to_dispatch]
        out_stream.write(json.dumps(requests) + "\n")
        out_stream.flush()
        # Stamp spawned_at on the queue so the next tick knows.
        stamp = datetime.now(timezone.utc).isoformat()
        for c in to_dispatch:
            c.spawned_at = stamp
            if c.source == "mempalace" and c.source_id:
                mark_flag_triggered(mempalace_mod, c.source_id)
        write_queue(fresh)
        bump_invocation_meter(mempalace_mod, when=now, count=len(to_dispatch))
        LOG.info("Inline mode: emitted %d WORKER_REQUEST(s).", len(to_dispatch))
        return 0

    # --- daemon mode: subprocess spawn, sequential, capped ---
    prompt_path = Path(WORKER_PROMPT_PATH).expanduser()
    if not prompt_path.is_file():
        LOG.error("Worker prompt missing: %s", prompt_path)
        return 1

    spawned = 0
    for c in to_dispatch:
        spawn_worker(c, prompt_path=prompt_path, runner=worker_runner)
        c.spawned_at = datetime.now(timezone.utc).isoformat()
        spawned += 1
        if c.source == "mempalace" and c.source_id:
            mark_flag_triggered(mempalace_mod, c.source_id)

    write_queue(fresh)
    bump_invocation_meter(mempalace_mod, when=now, count=spawned)
    LOG.info("Daemon mode: spawned %d worker(s).", spawned)
    return 0


if __name__ == "__main__":
    sys.exit(run())

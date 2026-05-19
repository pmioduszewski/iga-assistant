"""Trigger evaluators — turn a parsed ``Job.trigger`` into fired candidates.

WHY THIS EXISTS
---------------
``schema.py`` (Wave 1) only *parses* a trigger into ``kind`` + raw ``args``.
It deliberately does NOT execute anything. This module is the execution half:
given a parsed :class:`~engine.schema.Trigger`, query the real world and yield
zero or more :class:`Candidate` objects — one per concrete thing the job
should act on this tick.

DESIGN CONTRACT
---------------
* **Every external I/O is injectable.** Todoist HTTP, the MemPalace module,
  and "now" are all parameters with sane defaults, so tests mock them exactly
  the way ``iga-proactive-research/tests/test_scanner.py`` does (a
  ``types.SimpleNamespace`` fake for MemPalace, a fake fetcher for Todoist).
* **Graceful degradation, never raise on missing infra.** No Todoist token →
  yield nothing. MemPalace unavailable → yield nothing. This mirrors the old
  scanner: absence of a data source is not an error, it is just "no
  candidates". Transient API failures are swallowed to an empty list too —
  the engine reruns next tick.
* **The engine decides; this only detects.** No idempotency, budget, or
  cooldown logic here. That is the runtime's job (``runtime.py``) using the
  frozen Wave 1 ledger/governor.

CANDIDATE
---------
A :class:`Candidate` is the unit a trigger fires. It carries enough context
to (a) render the job's ``idempotency_key`` template and (b) hand the worker
something to act on. ``context`` is a flat ``dict[str, str]`` so the runtime
can do ``{{task.id}}`` style substitution without re-deriving anything.

Stdlib only (``urllib`` for HTTP).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import urllib.error
import urllib.request

try:  # package import (skills.iga-proactive.engine.*)
    from .schema import Trigger
except ImportError:  # flat import (engine/ on sys.path, repo's house pattern)
    from schema import Trigger  # type: ignore

LOG = logging.getLogger("iga_proactive.triggers")

# Same token sources the old scanner used (env first, then config file).
_TODOIST_TOKEN_FILE = "~/.config/todoist/token"
_TODOIST_API_BASE = "https://api.todoist.com/api/v1"


# --------------------------------------------------------------------------- #
# Candidate
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    """One concrete thing a trigger fired for.

    ``key`` is a stable, source-local identity (Todoist task id, drawer id,
    cron tick stamp, ...). ``context`` is the flat namespace the runtime uses
    to render ``idempotency_key`` / action templates.
    """

    trigger_kind: str
    source_id: str
    title: str
    context: dict[str, str] = field(default_factory=dict)

    def render_context(self) -> dict[str, str]:
        """Namespace used for ``{{...}}`` substitution by the runtime.

        Always includes ``trigger.kind``, ``source.id`` and ``candidate.title``
        plus every key the specific evaluator put in ``context``.
        """
        ns = {
            "trigger.kind": self.trigger_kind,
            "source.id": self.source_id,
            "candidate.title": self.title,
        }
        ns.update(self.context)
        return ns


# --------------------------------------------------------------------------- #
# Arg parsing — light, scoped to the shapes the trigger DSL actually uses
# --------------------------------------------------------------------------- #
def parse_kv_args(raw: str) -> dict[str, str]:
    """Parse ``label:iga-research, due:<7d`` → ``{"label": "iga-research",
    "due": "<7d"}``.

    Comma-separated ``key:value`` pairs. The value keeps everything after the
    FIRST colon (so ``due:<7d`` and ``window:48h`` survive intact). Whitespace
    around keys/values is stripped. A bare token with no colon is stored with
    an empty value (lets ``manual`` / ``watch(some_predicate)`` pass through).
    """
    out: dict[str, str] = {}
    if not raw or not raw.strip():
        return out
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            k, _, v = chunk.partition(":")
            out[k.strip()] = v.strip()
        else:
            out[chunk] = ""
    return out


_REL_DUE_RE = re.compile(r"^<\s*(\d+)\s*d$")


def _parse_due_days(spec: str | None) -> int | None:
    """``"<7d"`` → ``7``. Anything else (absolute date, missing) → None."""
    if not spec:
        return None
    m = _REL_DUE_RE.match(spec.strip())
    if not m:
        return None
    return int(m.group(1))


# --------------------------------------------------------------------------- #
# Token loading (identical sourcing to the old scanner)
# --------------------------------------------------------------------------- #
def load_todoist_token() -> str | None:
    tok = os.environ.get("TODOIST_API_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    p = Path(_TODOIST_TOKEN_FILE).expanduser()
    if p.is_file():
        txt = p.read_text(encoding="utf-8").strip()
        return txt or None
    return None


# --------------------------------------------------------------------------- #
# Default I/O implementations (all injectable for tests)
# --------------------------------------------------------------------------- #
def _default_todoist_fetch(token: str, label: str) -> list[dict[str, Any]]:
    """Query Todoist REST API v2 for open tasks carrying ``label``.

    Returns the raw task list. Network/HTTP errors are turned into an empty
    list (graceful — the engine retries next tick).
    """
    req = urllib.request.Request(
        f"{_TODOIST_API_BASE}/tasks?label={label}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        LOG.warning("Todoist fetch failed (transient?), yielding nothing: %s", exc)
        return []
    if isinstance(data, dict):
        return data.get("results", []) or []
    return data or []


def _import_mempalace():
    """Import the mempalace mcp_server module the old scanner used."""
    from mempalace import mcp_server  # type: ignore

    return mcp_server


# --------------------------------------------------------------------------- #
# Per-kind evaluators
# --------------------------------------------------------------------------- #
def _cron_field_matches(field_spec: str, value: int, lo: int, hi: int) -> bool:
    """Match one of the 5 standard cron fields against ``value``.

    Supports ``*``, ``*/n``, ``a-b``, ``a-b/n``, ``a,b,c`` and bare ints. No
    names (``MON``/``JAN``) — numeric only, which is all the schedule trigger
    needs. ``croniter`` is intentionally NOT a dependency.
    """
    for part in field_spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            rng, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                continue
            if step <= 0:
                continue
        else:
            rng = part
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, _, b = rng.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
        else:
            try:
                start = end = int(rng)
            except ValueError:
                continue
            step = 1
        if start < lo or end > hi or start > end:
            # Out-of-range field spec: treat as non-matching, do not crash.
            continue
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(expr: str, when: datetime) -> bool:
    """Minimal 5-field cron matcher: ``min hour dom month dow``.

    ``dow``: 0 or 7 == Sunday (both accepted). Match is evaluated at
    minute granularity for the tick that contains ``when``; the runtime is
    responsible for not ticking the same minute twice (the ledger cooldown +
    idempotency key on the minute stamp also guard this).
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron expression must have 5 fields, got {len(fields)}: {expr!r}"
        )
    minute, hour, dom, month, dow = fields
    w = when.astimezone(timezone.utc)
    py_dow = w.isoweekday() % 7  # Python isoweekday: Mon=1..Sun=7 → cron Sun=0
    if not _cron_field_matches(minute, w.minute, 0, 59):
        return False
    if not _cron_field_matches(hour, w.hour, 0, 23):
        return False
    if not _cron_field_matches(dom, w.day, 1, 31):
        return False
    if not _cron_field_matches(month, w.month, 1, 12):
        return False
    # dow: accept both 0 and 7 for Sunday by normalising "7" → "0" tokens.
    dow_norm = re.sub(r"\b7\b", "0", dow)
    if not _cron_field_matches(dow_norm, py_dow, 0, 6):
        return False
    return True


def eval_schedule(
    trigger: Trigger,
    *,
    now: datetime,
) -> list[Candidate]:
    """``schedule(<5-field cron>)`` → one candidate iff this tick matches.

    The candidate's ``source_id`` is the minute-resolution UTC stamp, so the
    rendered idempotency key is naturally unique per fired minute and the
    ledger dedups re-ticks of the same minute for free.
    """
    cron = trigger.args.strip()
    if not cron:
        LOG.warning("schedule() trigger has no cron expression; no candidates")
        return []
    try:
        fired = cron_matches(cron, now)
    except ValueError as exc:
        LOG.warning("Invalid cron %r: %s — no candidates", cron, exc)
        return []
    if not fired:
        return []
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    return [
        Candidate(
            trigger_kind="schedule",
            source_id=stamp,
            title=f"schedule {cron} @ {stamp}",
            context={"schedule.cron": cron, "schedule.tick": stamp},
        )
    ]


def eval_todoist(
    trigger: Trigger,
    *,
    now: datetime,
    token: str | None = None,
    fetcher: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> list[Candidate]:
    """``todoist(label:X, due:<Nd)`` → one candidate per matching open task.

    Token: explicit arg → ``$TODOIST_API_TOKEN`` → ``~/.config/todoist/token``
    (same precedence as the old scanner). No token → ``[]`` (graceful, no
    raise). ``due:<Nd`` filters to tasks due/deadline within the next N days
    and not already overdue (mirrors ``fetch_todoist_candidates``).
    """
    args = parse_kv_args(trigger.args)
    label = args.get("label")
    if not label:
        LOG.warning("todoist() trigger missing label: arg; no candidates")
        return []

    tok = token or load_todoist_token()
    if not tok:
        LOG.info("No Todoist token (env or ~/.config/todoist/token); no candidates")
        return []

    fetch = fetcher or _default_todoist_fetch
    try:
        tasks = fetch(tok, label)
    except Exception as exc:  # noqa: BLE001 — graceful, never raise out
        LOG.warning("Todoist fetcher raised, yielding nothing: %s", exc)
        return []

    due_days = _parse_due_days(args.get("due"))
    cutoff = (
        (now + timedelta(days=due_days)).date() if due_days is not None else None
    )
    today = now.astimezone(timezone.utc).date()

    out: list[Candidate] = []
    for task in tasks or []:
        due = task.get("due") or {}
        deadline = task.get("deadline") or {}
        target = due.get("date") or deadline.get("date")
        if due_days is not None:
            if not target:
                continue
            try:
                tdt = datetime.strptime(str(target)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if tdt > cutoff or tdt < today:
                continue
        title = (task.get("content") or "").strip()
        if not title:
            continue
        tid = str(task.get("id", ""))
        if not tid:
            continue
        ctx = {
            "task.id": tid,
            "task.title": title,
            "task.due": str(target or ""),
            "task.context": (task.get("description") or "")[:600],
            "task.label": label,
        }
        out.append(
            Candidate(
                trigger_kind="todoist",
                source_id=tid,
                title=title,
                context=ctx,
            )
        )
    return out


_FLAG_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]*?)\s*:\s*(.*)$")

# Canonical structured-content keys a flag drawer encodes (see
# skills/newsletter-research/docs/hook-spec.md "Canonical flag-drawer
# schema"). The real `mempalace_add_drawer` MCP tool exposes ONLY
# wing/room/content/added_by/source_file — there is NO `metadata=` param and
# `tool_list_drawers` returns `drawer_id`/`content_preview` (no `metadata`,
# no full `content`). So producers MUST encode these as `key: value` lines
# inside `content`; this parser is the read side of that contract.
_FLAG_CONTENT_KEYS = {
    "hook_name",
    "title",
    "target_date",
    "triggered",
    "message-id",
    "message_id",
}


def parse_flag_content(content: str) -> dict[str, str]:
    """Parse the structured ``key: value`` lines a flag drawer encodes in its
    ``content`` (the only place they can live — the MCP add_drawer tool has no
    ``metadata=`` param).

    Tolerant: a leading non-key banner line (e.g.
    ``NEWSLETTER-RESEARCH-QUEUE FLAG``) is ignored; only recognised keys are
    extracted; unknown ``k: v`` lines are skipped so free-text context never
    pollutes the namespace. ``message_id`` is normalised to ``message-id``.
    Pure / side-effect-free.
    """
    out: dict[str, str] = {}
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _FLAG_FIELD_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        if key not in _FLAG_CONTENT_KEYS:
            continue
        if key == "message_id":
            key = "message-id"
        out[key] = m.group(2).strip()
    return out


def _drawer_field(d: dict, *names: str) -> str:
    """First non-empty value among ``names`` in a drawer dict.

    The real ``tool_list_drawers`` returns ``drawer_id`` + ``content_preview``;
    the test fakes (and the old scanner's contract) use ``id`` + ``content``.
    Read both so the trigger works against the live MCP AND the unit fakes.
    """
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            return str(v)
    return ""


def eval_mempalace(
    trigger: Trigger,
    *,
    now: datetime,
    mempalace_mod: Any | None = None,
) -> list[Candidate]:
    """``mempalace(room:X, ...)`` → one candidate per non-triggered drawer.

    Uses ``mempalace_mod.tool_list_drawers(room=..., limit=...)`` — the exact
    invocation the old scanner used. Drawers whose ``triggered`` marker is
    truthy are skipped (already consumed). If the module can't be imported or
    the call fails, returns ``[]`` (graceful — no raise).

    Field resolution is contract-reconciled (see
    ``skills/newsletter-research/docs/hook-spec.md``): the real
    ``mempalace_add_drawer`` MCP tool has **no** ``metadata=`` param and
    ``tool_list_drawers`` returns ``drawer_id``/``content_preview`` (no
    ``metadata``, no full ``content``). So per drawer we resolve each field
    with this precedence:

      1. ``drawer.metadata.<field>`` (legacy / test-fake shape — wins if present)
      2. structured ``<field>: value`` line parsed out of the drawer body
         (``content`` or ``content_preview``) — the real-MCP path

    ``drawer.id`` falls back to ``drawer_id``; the body falls back to
    ``content_preview``. ``hook_name`` (when present) is exported as
    ``drawer.hook_name`` so the worker/runner can load the right hook spec
    without re-parsing the body.
    """
    args = parse_kv_args(trigger.args)
    room = args.get("room")
    if not room:
        LOG.warning("mempalace() trigger missing room: arg; no candidates")
        return []

    mod = mempalace_mod
    if mod is None:
        try:
            mod = _import_mempalace()
        except Exception as exc:  # noqa: BLE001 — graceful
            # LOUD: a misconfigured runtime (wrong interpreter / mempalace
            # not importable) is the #1 cause of a silent zero-candidate
            # no-op. Warn, don't whisper at INFO.
            LOG.warning(
                "mempalace trigger DEGRADED (room=%s): cannot import the "
                "mempalace package → 0 candidates. The scan must run with a "
                "python that has `mempalace` importable. Underlying: %s",
                room,
                exc,
            )
            return []

    try:
        result = mod.tool_list_drawers(room=room, limit=50)
    except Exception as exc:  # noqa: BLE001 — graceful
        LOG.warning("mempalace list_drawers failed, no candidates: %s", exc)
        return []

    if isinstance(result, dict) and result.get("error"):
        LOG.warning("mempalace error, no candidates: %s", result["error"])
        return []

    drawers = result.get("drawers", []) if isinstance(result, dict) else []
    out: list[Candidate] = []
    for d in drawers:
        meta = d.get("metadata") or {}
        # Body is `content` (test-fake / get_drawer) or `content_preview`
        # (real tool_list_drawers). Parse structured flag fields out of it.
        body = _drawer_field(d, "content", "content_preview")
        parsed = parse_flag_content(body)

        def _field(name: str, default: str = "") -> str:
            # metadata wins (legacy / test fakes), else parsed-from-content.
            mv = meta.get(name)
            if mv not in (None, ""):
                return str(mv)
            return parsed.get(name, default)

        if _field("triggered", "false").lower() == "true":
            continue

        did = _drawer_field(d, "id", "drawer_id")
        if not did:
            continue

        title = (_field("title") or body[:80]).strip()
        if not title:
            continue

        target = _field("target_date") or now.astimezone(
            timezone.utc
        ).date().isoformat()
        hook_name = _field("hook_name")
        message_id = _field("message-id")
        ctx = {
            "drawer.id": did,
            "drawer.title": title,
            "drawer.room": room,
            "drawer.target_date": str(target),
            "drawer.context": body[:600],
        }
        if hook_name:
            ctx["drawer.hook_name"] = hook_name
        if message_id:
            ctx["drawer.message_id"] = message_id
        out.append(
            Candidate(
                trigger_kind="mempalace",
                source_id=did,
                title=title,
                context=ctx,
            )
        )
    return out


def eval_manual(trigger: Trigger, **_: Any) -> list[Candidate]:
    """``manual`` → always one eligible candidate.

    The runtime still gates it through the ledger cooldown + governor, so a
    ``manual`` job won't fire-loop: it produces a stable candidate whose
    idempotency key (rendered by the runtime) typically pins it per cooldown
    window. ``source_id`` is the literal ``"manual"`` so the key is stable.
    """
    return [
        Candidate(
            trigger_kind="manual",
            source_id="manual",
            title="manual trigger",
            context={"manual": "true"},
        )
    ]


def eval_calendar(trigger: Trigger, **_: Any) -> list[Candidate]:
    # WAVE3: calendar(window:Nh) needs a calendar data source (Google Calendar
    # MCP / ICS feed) that is out of the Wave 2 fence. Interface is fixed:
    # args carry `window:Nh`; an injected calendar fetcher will yield one
    # Candidate per event whose start falls inside [now, now+window]. The old
    # scanner deliberately dropped calendar in v2 (Todoist due dates already
    # carry the temporal signal), so the research port does not need this.
    raise NotImplementedError(
        "calendar() trigger is a Wave 3 stub — provide a calendar fetcher; "
        "research port uses todoist/mempalace which ARE implemented"
    )


def eval_watch(trigger: Trigger, **_: Any) -> list[Candidate]:
    # WAVE3: watch(predicate) needs a file/state watcher loop + a predicate
    # evaluator, both of which require the daemon/menu-bar entrypoint that is
    # explicitly out of the Wave 2 fence. Interface is fixed: args carry the
    # raw predicate; an injected state resolver will yield one Candidate when
    # the predicate flips false→true since the last tick.
    raise NotImplementedError(
        "watch() trigger is a Wave 3 stub — needs the entrypoint watch loop; "
        "research port uses todoist/mempalace which ARE implemented"
    )


# --------------------------------------------------------------------------- #
# Dispatch table + public entrypoint
# --------------------------------------------------------------------------- #
_EVALUATORS: dict[str, Callable[..., list[Candidate]]] = {
    "schedule": eval_schedule,
    "todoist": eval_todoist,
    "mempalace": eval_mempalace,
    "manual": eval_manual,
    "calendar": eval_calendar,
    "watch": eval_watch,
}


def evaluate(
    trigger: Trigger,
    *,
    now: datetime | None = None,
    token: str | None = None,
    todoist_fetcher: Callable[[str, str], list[dict[str, Any]]] | None = None,
    mempalace_mod: Any | None = None,
) -> list[Candidate]:
    """Evaluate a parsed trigger → fired candidates.

    All external I/O is injected (``token``, ``todoist_fetcher``,
    ``mempalace_mod``) so this is fully unit-testable with no network or MCP.
    ``calendar``/``watch`` raise ``NotImplementedError`` (Wave 3 stubs); the
    runtime catches that and skips the job with a logged warning so one
    unimplemented trigger never breaks a whole scan tick.
    """
    now = now or datetime.now(timezone.utc)
    fn = _EVALUATORS.get(trigger.kind)
    if fn is None:
        raise ValueError(f"no evaluator for trigger kind {trigger.kind!r}")
    if trigger.kind == "schedule":
        return fn(trigger, now=now)
    if trigger.kind == "todoist":
        return fn(trigger, now=now, token=token, fetcher=todoist_fetcher)
    if trigger.kind == "mempalace":
        return fn(trigger, now=now, mempalace_mod=mempalace_mod)
    # manual / calendar / watch
    return fn(trigger, now=now)

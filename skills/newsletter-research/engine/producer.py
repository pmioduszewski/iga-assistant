#!/usr/bin/env python3
"""Newsletter-research PRODUCER — the queue feeder (stdlib only).

WHY THIS EXISTS
---------------
STEP 1 shipped a generic spec-driven email-hook RUNNER: a ``proactive.yaml``
job the generic ``skills/iga-proactive`` engine discovers, triggered by a
MemPalace room poll on ``newsletter-research-queue``. The room is empty by
default — *that empty room is the killswitch* — so the runner is dormant
until a flag drawer is hand-filed.

This module is the missing half: a **generic producer** that scans every
personal hook's Gmail trigger query and files ONE idempotent flag drawer per
matching message into ``newsletter-research-queue``. With the producer in
place the runner becomes self-sustaining instead of hand-fed, while the
killswitch property is *preserved* (see SAFETY below).

GENERIC, NO PII
---------------
The producer iterates **every** ``rules/hooks/*.md`` (personal layer,
gitignored) — it is NOT hardcoded to ``dev-libs``. The hook spec parser is
the shipped, generic ``hook_spec.parse_hook_spec``. No interests, names, or
addresses live in this file. ``rules/hooks/`` is the user's; this engine
never creates or edits it.

INJECTABLE I/O (mirrors triggers.py / scanner.py contract)
----------------------------------------------------------
Every external dependency is a parameter with a safe default so the whole
producer is unit-testable with no Gmail, no MCP, no network:

  * ``gmail_search`` — ``Callable[[str], list[dict]]``: a Gmail search-query
    runner returning message stubs (``{"id": ..., "subject": ..., ...}``).
    Default: a no-op that yields ``[]`` (no Gmail wired → nothing produced —
    graceful, exactly like ``triggers.eval_todoist`` with no Todoist token).
  * ``mempalace_mod`` — the MemPalace module (``tool_add_drawer`` /
    ``tool_list_drawers``). Default: imported lazily; absent → nothing filed.
  * ``ledger`` / ``governor`` — the FROZEN Wave-1 services. The producer does
    NOT re-implement idempotency or budgeting; it *delegates* to
    ``Ledger.claim`` (per message-id) and ``Governor.allow`` exactly as
    ``runtime.scan_tick`` does for the consumer side.

IDEMPOTENCY — TWO INDEPENDENT GUARANTEES
----------------------------------------
1. **Content-addressed drawer id.** ``mempalace_add_drawer`` derives the
   drawer id from ``sha256(wing+room+content)`` and is a no-op if it already
   exists. Because the producer writes a *deterministic* canonical body for a
   given (hook, message-id, target_date), re-filing the same message is a
   server-side no-op — never a duplicate drawer.
2. **Producer ledger claim.** Before filing, the producer
   ``Ledger.claim``-s a key ``nl-produce::<hook>::<message-id>`` with a
   cooldown. A second tick within cooldown loses the claim and files
   nothing — so we don't hammer ``tool_add_drawer`` every scan even though
   (1) would absorb it. This mirrors the consumer's anti-duplicate point.

SAFETY (killswitch preserved & documented)
------------------------------------------
* ``IGA_PROACTIVE_RESEARCH=0`` — hard killswitch: produce nothing, exit 0.
* ``IGA_PROACTIVE_SPAWN=0`` — detect-but-don't-mutate: scan + log what WOULD
  be filed, file NOTHING (no drawer, no ledger row). Same env the consumer
  honours; shared dial.
* No hooks / no Gmail / empty results → zero drawers filed. The
  ``newsletter-research-queue`` room stays empty → the consumer killswitch
  property is intact (an empty room still spawns nothing). The producer can
  only ever ADD to the queue when a real hook matches a real message; it
  never changes the consumer's empty-room semantics.
* Per-tick cap (``IGA_MAX_SPAWN_PER_TICK``, default 3) bounds how many new
  flag drawers a single tick files — the same throttle name the engine uses.

CANONICAL FLAG-DRAWER SCHEMA (the contract reconciliation)
----------------------------------------------------------
The real ``mempalace_add_drawer`` MCP tool signature is
``(wing, room, content, source_file=None, added_by="mcp")`` — there is **no**
``metadata=`` param, and ``tool_list_drawers`` returns
``drawer_id``/``content_preview`` (no ``metadata``, no full ``content``).
STEP-1 ``SKILL.md`` documented a ``metadata={title,target_date,hook_name}``
schema that the tool cannot accept. RESOLUTION: the fields are encoded as
structured ``key: value`` lines inside ``content``; the trigger
(``triggers.parse_flag_content`` / ``eval_mempalace``) reads them back from
content with a metadata-wins fallback. This producer writes EXACTLY that
canonical body (``flag_drawer_body``), so producer-write and trigger-read are
the same shape end to end. Schema documented in
``skills/newsletter-research/docs/hook-spec.md``.
"""

from __future__ import annotations

import dataclasses
import glob
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # package import (skills.newsletter-research.engine.*)
    from .hook_spec import HookSpecError, parse_hook_spec
except ImportError:  # flat import (engine/ on sys.path — repo house style)
    from hook_spec import HookSpecError, parse_hook_spec  # type: ignore

LOG = logging.getLogger("newsletter_research.producer")

QUEUE_ROOM = "newsletter-research-queue"
# Wing the flag drawers live in. The consumer trigger is room-scoped
# (`mempalace(room:newsletter-research-queue)`), so wing is bookkeeping only;
# a stable generic wing keeps the content-addressed id deterministic.
QUEUE_WING = "iga/newsletter-research"
FLAG_BANNER = "NEWSLETTER-RESEARCH-QUEUE FLAG"
DEFAULT_MAX_PER_TICK = 3
# Producer cooldown: long enough that a message isn't re-claimed every scan,
# short enough that a transient MCP failure self-heals next day. Independent
# of the consumer's 72h spawn cooldown.
PRODUCER_COOLDOWN_SECONDS = 24 * 3600
DEFAULT_HOOKS_GLOB = "~/Gaia/rules/hooks/*.md"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ProducedFlag:
    """One flag drawer the producer decided to file (pre-MCP-write)."""

    hook_name: str
    message_id: str
    title: str
    target_date: str  # YYYY-MM-DD
    gmail_query: str
    label: str = ""

    @property
    def idempotency_key(self) -> str:
        """Producer-side ledger key. Stable per (hook, message-id) — the
        target_date is intentionally NOT in the key so a message re-seen on a
        later day is still recognised as the same already-queued message."""
        return f"nl-produce::{self.hook_name}::{self.message_id}"

    def content(self) -> str:
        """Canonical flag-drawer body — see
        ``skills/newsletter-research/docs/hook-spec.md``. This is the EXACT
        shape ``triggers.parse_flag_content`` reads back."""
        lines = [
            FLAG_BANNER,
            f"hook_name: {self.hook_name}",
            f"title: {self.title}",
            f"target_date: {self.target_date}",
            f"message-id: {self.message_id}",
            "triggered: false",
        ]
        if self.label:
            lines.append(f"label: {self.label}")
        if self.gmail_query:
            lines.append(f"gmail_query: {self.gmail_query}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Killswitch helpers (identical semantics to scanner.py / cli.py)
# --------------------------------------------------------------------------- #
def killswitch_active() -> bool:
    return os.environ.get("IGA_PROACTIVE_RESEARCH", "1") == "0"


def spawn_disabled() -> bool:
    return os.environ.get("IGA_PROACTIVE_SPAWN", "1") == "0"


def _max_per_tick() -> int:
    raw = os.environ.get("IGA_MAX_SPAWN_PER_TICK")
    if raw is None:
        return DEFAULT_MAX_PER_TICK
    try:
        return max(0, int(raw))
    except ValueError:
        LOG.warning(
            "Invalid IGA_MAX_SPAWN_PER_TICK=%r, falling back to %d",
            raw,
            DEFAULT_MAX_PER_TICK,
        )
        return DEFAULT_MAX_PER_TICK


# --------------------------------------------------------------------------- #
# Hook discovery + query derivation
# --------------------------------------------------------------------------- #
def discover_hook_specs(
    hooks_glob: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Parse every ``rules/hooks/*.md`` into ``(path, spec)`` pairs.

    GENERIC: globs the personal hooks dir, never hardcodes a hook name. A
    spec that fails validation is logged and skipped (one bad personal hook
    never blocks the others — same resilience as ``runtime.load_jobs``).
    Missing dir / no hooks → ``[]`` (graceful; nothing to produce).
    """
    pattern = os.path.expanduser(hooks_glob or DEFAULT_HOOKS_GLOB)
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(glob.glob(pattern)):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            LOG.warning("Hook spec unreadable %s: %s", path, exc)
            continue
        try:
            spec = parse_hook_spec(text)
        except HookSpecError as exc:
            LOG.warning("Invalid hook spec %s: %s — skipping", path, exc)
            continue
        out.append((path, spec))
    return out


def derive_gmail_query(spec: dict[str, Any]) -> tuple[str, str]:
    """Return ``(gmail_query, label)`` for a parsed hook spec.

    ``trigger.gmail_query`` is used verbatim. ``trigger.gmail_label`` is
    turned into a ``label:"<value>"`` query (quoted so labels with spaces /
    slashes — e.g. ``Newsletter/Dev`` — survive Gmail's query parser).
    Exactly one of the two is present (``hook_spec`` enforces that).
    """
    trig = spec.get("trigger") or {}
    q = (trig.get("gmail_query") or "").strip()
    if q:
        return q, ""
    label = (trig.get("gmail_label") or "").strip()
    if label:
        return f'label:"{label}"', label
    return "", ""


# --------------------------------------------------------------------------- #
# Gmail search seam (injected; default = nothing wired)
# --------------------------------------------------------------------------- #
def _null_gmail_search(query: str) -> list[dict[str, Any]]:
    """Default Gmail seam: no Gmail wired → no messages.

    Graceful degradation, never raise — mirrors ``triggers.eval_todoist``
    with no token. A real entrypoint injects a closure over the iga-gmail
    MCP ``manage_email`` search.
    """
    LOG.info("No gmail_search injected; producing nothing for %r", query)
    return []


def _message_id(msg: dict[str, Any]) -> str:
    for k in ("id", "message_id", "message-id", "messageId", "rfc822msgid"):
        v = msg.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _message_title(msg: dict[str, Any], hook_name: str) -> str:
    for k in ("subject", "title", "snippet"):
        v = msg.get(k)
        if v not in (None, ""):
            return f"Newsletter/{hook_name}: {str(v).strip()[:120]}"
    return f"Newsletter/{hook_name}: (no subject)"


# --------------------------------------------------------------------------- #
# Core: scan hooks → file flag drawers
# --------------------------------------------------------------------------- #
def _import_mempalace():
    from mempalace import mcp_server  # type: ignore

    return mcp_server


def collect_flags(
    *,
    now: datetime,
    hooks_glob: str | None = None,
    gmail_search: Callable[[str], list[dict[str, Any]]] | None = None,
) -> list[ProducedFlag]:
    """Deterministic detection: every active hook × every matching message.

    Pure w.r.t. the injected ``gmail_search`` (no MCP, no ledger here — that
    is the gating layer's job, exactly like ``triggers.evaluate`` only
    detects and ``runtime.scan_tick`` gates). ``paused`` hooks are skipped.
    Dedupes (hook, message-id) within a single tick so two queries that both
    match a message yield one flag.
    """
    search = gmail_search or _null_gmail_search
    target = now.astimezone(timezone.utc).date().isoformat()
    seen: set[tuple[str, str]] = set()
    out: list[ProducedFlag] = []
    for path, spec in discover_hook_specs(hooks_glob):
        if spec.get("status") == "paused":
            LOG.info("Hook %s is paused; skipping", spec.get("name"))
            continue
        query, label = derive_gmail_query(spec)
        if not query:
            LOG.warning("Hook %s has no usable trigger query; skipping", path)
            continue
        try:
            messages = search(query) or []
        except Exception as exc:  # noqa: BLE001 — graceful, never raise out
            LOG.warning(
                "gmail_search raised for hook %s (%r): %s — skipping",
                spec.get("name"),
                query,
                exc,
            )
            continue
        hook_name = str(spec.get("name") or "")
        for msg in messages:
            mid = _message_id(msg)
            if not mid:
                continue
            dedup = (hook_name, mid)
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append(
                ProducedFlag(
                    hook_name=hook_name,
                    message_id=mid,
                    title=_message_title(msg, hook_name),
                    target_date=target,
                    gmail_query=query,
                    label=label,
                )
            )
    return out


def file_flag(mempalace_mod: Any, flag: ProducedFlag) -> dict[str, Any]:
    """File ONE flag drawer via the real MCP tool signature.

    Uses ONLY ``(wing, room, content, source_file, added_by)`` — the params
    the live ``mempalace_add_drawer`` actually exposes. No ``metadata=``
    (it does not exist). Content-addressed id makes a re-file a no-op.
    """
    return mempalace_mod.tool_add_drawer(
        wing=QUEUE_WING,
        room=QUEUE_ROOM,
        content=flag.content(),
        source_file=f"rules/hooks/{flag.hook_name}.md",
        added_by="newsletter-research-producer",
    )


def produce(
    *,
    now: datetime | None = None,
    hooks_glob: str | None = None,
    gmail_search: Callable[[str], list[dict[str, Any]]] | None = None,
    mempalace_mod: Any | None = None,
    ledger: Any | None = None,
    governor: Any | None = None,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Run one producer tick. Returns a summary dict (never raises).

    Pipeline (mirrors ``runtime.scan_tick`` admission order):
      1. killswitch (``IGA_PROACTIVE_RESEARCH=0``) → no-op.
      2. detect: ``collect_flags`` (deterministic, injected Gmail).
      3. per flag: ``Ledger.claim(nl-produce::<hook>::<mid>)`` — the
         producer anti-duplicate point (lost claim = already queued, skip).
      4. ``Governor.allow`` — denied → ``ledger.mark(failed)``, skip
         (cooldown still holds; no retry-storm).
      5. ``IGA_PROACTIVE_SPAWN=0`` → mark the claim ``failed`` (release-ish:
         cooldown short) and DO NOT write the drawer (detect-only).
      6. else ``tool_add_drawer`` then ``ledger.mark(done)``.
      7. per-tick cap (``IGA_MAX_SPAWN_PER_TICK``).
    """
    now = now or datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "detected": 0,
        "filed": 0,
        "claim_skipped": 0,
        "governor_denied": 0,
        "spawn_disabled": False,
        "killswitched": False,
        "capped": 0,
        "errors": [],
    }

    if killswitch_active():
        LOG.info("IGA_PROACTIVE_RESEARCH=0 — producer disabled, nothing filed.")
        summary["killswitched"] = True
        return summary

    # Lazy frozen-service wiring (injected in tests). The producer NEVER
    # re-implements idempotency/budgeting — it delegates to the frozen
    # Wave-1 Ledger/Governor exactly as runtime.scan_tick does.
    if ledger is None or governor is None:
        try:
            import sys

            eng = Path(__file__).resolve().parents[2] / "iga-proactive" / "engine"
            if str(eng) not in sys.path:
                sys.path.insert(0, str(eng))
            from ledger import Ledger  # type: ignore
            from governor import Governor  # type: ignore

            ledger = ledger or Ledger(db_path)
            governor = governor or Governor(db_path)
        except Exception as exc:  # noqa: BLE001 — no engine → can't gate, bail safe
            LOG.error("Could not load frozen ledger/governor: %s", exc)
            summary["errors"].append(f"engine-unavailable: {exc}")
            return summary

    flags = collect_flags(now=now, hooks_glob=hooks_glob, gmail_search=gmail_search)
    summary["detected"] = len(flags)
    if not flags:
        return summary

    mod = mempalace_mod
    if mod is None:
        try:
            mod = _import_mempalace()
        except Exception as exc:  # noqa: BLE001 — graceful
            LOG.info("mempalace unavailable, nothing filed: %s", exc)
            summary["errors"].append(f"mempalace-unavailable: {exc}")
            return summary

    detect_only = spawn_disabled()
    summary["spawn_disabled"] = detect_only
    cap = _max_per_tick()
    filed = 0

    for flag in flags:
        if cap >= 0 and filed >= cap:
            summary["capped"] += 1
            continue

        key = flag.idempotency_key
        if ledger.should_skip(key):
            summary["claim_skipped"] += 1
            continue
        if not ledger.claim(key, "newsletter-research-producer", PRODUCER_COOLDOWN_SECONDS):
            summary["claim_skipped"] += 1
            continue

        # Budget gate — small fixed cost per flag (a drawer write, not a
        # worker run). Keep it cheap but accounted so a runaway producer
        # still trips the governor breaker.
        decision = governor.allow("none", 0)
        if not decision.ok:
            ledger.mark(key, "failed")
            summary["governor_denied"] += 1
            LOG.info("Governor denied producer flag %s: %s", key, decision.reason)
            continue

        if detect_only:
            # Detect-but-don't-mutate: release the claim so a real later run
            # (spawn enabled) can file it. mark(failed) keeps the short
            # producer cooldown — no drawer, no queue mutation this tick.
            ledger.mark(key, "failed")
            LOG.info("IGA_PROACTIVE_SPAWN=0 — WOULD file flag %s (not filed)", key)
            continue

        try:
            res = file_flag(mod, flag)
        except Exception as exc:  # noqa: BLE001 — graceful per-flag
            ledger.mark(key, "failed")
            summary["errors"].append(f"{key}: add_drawer raised: {exc}")
            continue

        if isinstance(res, dict) and res.get("success") is False:
            ledger.mark(key, "failed")
            summary["errors"].append(f"{key}: add_drawer failed: {res.get('error')}")
            continue

        ledger.mark(key, "done")
        filed += 1
        summary["filed"] = filed
        LOG.info(
            "Filed newsletter-research-queue flag: hook=%s msg=%s",
            flag.hook_name,
            flag.message_id,
        )

    return summary

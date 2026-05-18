#!/usr/bin/env python3
"""Unhooked-cluster offer detector (stdlib only).

WHY THIS EXISTS
---------------
The producer feeds the runner from hooks the user already authored. But
high-value newsletter clusters often sit in labeled mail with **no hook
covering them** — the user never gets the value because they never set up
the hook. This detector finds those gaps and, when a threshold is crossed,
emits **exactly ONE** surfaced offer:

    "📨 You get ~N newsletters/wk in <K> streams with no research hook —
     want a 5-min brief + set one up?"

…delivered through the engine's normal surface path (a gitignored JSON
state file the ``/gm`` / ``/back`` entrypoint reads), mirroring how
``skills/iga-proactive/engine/surfacer.py`` + ``dispatcher.py`` write the
engine state file. The engine never pushes/interrupts — it parks an offer
for the next natural touchpoint.

NO PII (hard contract — CLAUDE.md three-layer composability)
------------------------------------------------------------
* A cluster is keyed by a **salted SHA1 prefix** of its raw identity
  (sender domain or label). The raw value NEVER leaves this process and
  NEVER lands in the surfaced payload or the state file.
* The surfaced offer is generic counts only ("N messages across K streams")
  — no addresses, subjects, domains, or names.
* The detector reads labeled-mail metadata via an **injected** counter
  callable; default = nothing wired → no offer. Fully unit-testable with no
  Gmail, no MCP, no network.

GENERIC
-------
Coverage is computed against EVERY ``rules/hooks/*.md`` (via the shipped
``producer.discover_hook_specs`` / ``derive_gmail_query``), not a hardcoded
hook. A stream is "covered" if a hook's label/query plausibly matches it.

IDEMPOTENT OFFER
----------------
Exactly one offer is live at a time. The offer carries a content hash of
the unhooked-cluster set; re-running with the same gap rewrites the same
offer (no spam). Crossing back under threshold clears it. The state file is
written atomically (tmp + os.replace) — a polling reader never sees a
half-written file (same guarantee as ``dispatcher._atomic_write_json``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # package import
    from .producer import derive_gmail_query, discover_hook_specs
except ImportError:  # flat import (engine/ on sys.path)
    from producer import derive_gmail_query, discover_hook_specs  # type: ignore

LOG = logging.getLogger("newsletter_research.unhooked")

# Default: offer when >= 3 unhooked streams carry >= 8 messages combined in
# the lookback window. Conservative so the offer is rare and high-signal.
DEFAULT_MIN_STREAMS = 3
DEFAULT_MIN_MESSAGES = 8
SCHEMA_VERSION = 1
# scratch/ is gitignored (verified) — keeps `git status` clean by construction.
DEFAULT_OFFER_PATH = "~/Gaia/scratch/newsletter-unhooked-offer.json"
# Process-local salt: the cluster key is stable WITHIN a run/state file but
# the raw identity is never recoverable from the persisted hash.
_SALT = "iga-newsletter-unhooked-v1"


def offer_path() -> Path:
    """``$IGA_NL_UNHOOKED_OFFER`` if set, else the gitignored scratch path."""
    env = os.environ.get("IGA_NL_UNHOOKED_OFFER")
    return Path(env).expanduser() if env else Path(DEFAULT_OFFER_PATH).expanduser()


def _cluster_key(raw_identity: str) -> str:
    """Salted SHA1 prefix. The raw sender-domain/label is NEVER persisted —
    this one-way hash is all that ever reaches disk or the surfaced offer."""
    return hashlib.sha1(
        f"{_SALT}|{(raw_identity or '').strip().lower()}".encode("utf-8")
    ).hexdigest()[:12]


def _covered_tokens(hooks_glob: str | None) -> list[str]:
    """Lowercased label/query fragments from every hook — the coverage set.

    A cluster identity that contains any covered token is considered hooked.
    Generic: derived from the user's actual hooks, nothing hardcoded.
    """
    tokens: list[str] = []
    for _path, spec in discover_hook_specs(hooks_glob):
        if spec.get("status") == "paused":
            continue
        query, label = derive_gmail_query(spec)
        if label:
            tokens.append(label.strip().lower())
        # Pull bare label:/from: operands out of a raw query too.
        for part in (query or "").replace('"', " ").split():
            if ":" in part:
                _op, _, val = part.partition(":")
                val = val.strip().lower()
                if val:
                    tokens.append(val)
            elif part.strip():
                tokens.append(part.strip().lower())
    return [t for t in tokens if t]


def _is_covered(identity: str, covered: list[str]) -> bool:
    ident = (identity or "").strip().lower()
    if not ident:
        return False
    return any(tok and tok in ident for tok in covered)


def _null_stream_counts(_lookback_days: int) -> dict[str, int]:
    """Default seam: no Gmail wired → no streams → no offer (graceful)."""
    LOG.info("No stream_counts injected; no unhooked-cluster offer.")
    return {}


def detect(
    *,
    now: datetime | None = None,
    hooks_glob: str | None = None,
    stream_counts: Callable[[int], dict[str, int]] | None = None,
    lookback_days: int = 7,
    min_streams: int = DEFAULT_MIN_STREAMS,
    min_messages: int = DEFAULT_MIN_MESSAGES,
) -> dict[str, Any]:
    """Compute the unhooked-cluster picture. Pure w.r.t. injected counter.

    ``stream_counts(lookback_days)`` returns ``{raw_identity: msg_count}``
    for newsletter-ish streams (sender domain or label) in the window. The
    raw identities are consumed here and immediately hashed; only counts +
    salted keys leave this function.

    Returns a dict: ``{unhooked_streams, unhooked_messages, threshold_met,
    cluster_fingerprint, clusters:[{key,count}]}``. No raw identity anywhere.
    """
    now = now or datetime.now(timezone.utc)
    counter = stream_counts or _null_stream_counts
    try:
        raw = counter(lookback_days) or {}
    except Exception as exc:  # noqa: BLE001 — graceful, never raise out
        LOG.warning("stream_counts raised: %s — no offer", exc)
        raw = {}

    covered = _covered_tokens(hooks_glob)
    clusters: list[dict[str, Any]] = []
    total_msgs = 0
    for identity, count in sorted(raw.items()):
        try:
            c = int(count)
        except (TypeError, ValueError):
            continue
        if c <= 0 or _is_covered(identity, covered):
            continue
        clusters.append({"key": _cluster_key(identity), "count": c})
        total_msgs += c

    clusters.sort(key=lambda d: (-d["count"], d["key"]))
    n_streams = len(clusters)
    threshold_met = n_streams >= min_streams and total_msgs >= min_messages
    fingerprint = hashlib.sha1(
        "|".join(f"{c['key']}:{c['count']}" for c in clusters).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "unhooked_streams": n_streams,
        "unhooked_messages": total_msgs,
        "threshold_met": threshold_met,
        "cluster_fingerprint": fingerprint,
        "clusters": clusters,
    }


def build_offer(detection: dict[str, Any]) -> dict[str, Any] | None:
    """Build the single surfaced offer payload from a detection, or ``None``
    if threshold not met. Generic counts only — no PII. AskUserQuestion-style
    shape the entrypoint renders at the next ``/gm`` / ``/back``.
    """
    if not detection.get("threshold_met"):
        return None
    n = detection["unhooked_streams"]
    m = detection["unhooked_messages"]
    days = detection.get("lookback_days", 7)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "newsletter-unhooked-offer",
        "generated_at": detection["generated_at"],
        "fingerprint": detection["cluster_fingerprint"],
        "deliver": "surface_next_brief",
        "headline": (
            f"📨 {m} newsletter messages across {n} streams in the last "
            f"{days}d have no research hook"
        ),
        "question": {
            "header": "Email hooks",
            "prompt": (
                f"You receive ~{m} messages across {n} unhooked newsletter "
                f"streams. Want a 5-min brief and to set up a research hook "
                f"so Iga mines them for you?"
            ),
            "options": [
                {
                    "label": "Yes — 5-min brief + set up a hook (Recommended)",
                    "description": (
                        "Iga summarises the top unhooked streams and walks "
                        "you through authoring rules/hooks/<name>.md"
                    ),
                },
                {
                    "label": "Not now",
                    "description": "Re-offer only if the gap grows",
                },
            ],
        },
        # Hashed cluster keys only — provably no sender/domain/subject.
        "clusters": detection["clusters"],
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def run(
    *,
    now: datetime | None = None,
    hooks_glob: str | None = None,
    stream_counts: Callable[[int], dict[str, int]] | None = None,
    lookback_days: int = 7,
    min_streams: int = DEFAULT_MIN_STREAMS,
    min_messages: int = DEFAULT_MIN_MESSAGES,
    state_path: Path | str | None = None,
    write_state: bool = True,
) -> dict[str, Any]:
    """One detector tick. Honours the shared killswitches, never raises.

    * ``IGA_PROACTIVE_RESEARCH=0`` → no-op, clears nothing, writes nothing.
    * ``IGA_PROACTIVE_SPAWN=0`` → detect + return the offer for inspection
      but write NO state file (detect-but-don't-mutate, same dial as the
      producer / consumer).
    * Threshold met → write exactly ONE offer to the gitignored state file.
    * Threshold NOT met → clear any stale offer file (return to dormant).
    """
    if os.environ.get("IGA_PROACTIVE_RESEARCH", "1") == "0":
        LOG.info("IGA_PROACTIVE_RESEARCH=0 — unhooked detector disabled.")
        return {"killswitched": True, "offer": None}

    detection = detect(
        now=now,
        hooks_glob=hooks_glob,
        stream_counts=stream_counts,
        lookback_days=lookback_days,
        min_streams=min_streams,
        min_messages=min_messages,
    )
    offer = build_offer(detection)
    path = Path(state_path).expanduser() if state_path else offer_path()

    detect_only = os.environ.get("IGA_PROACTIVE_SPAWN", "1") == "0"
    if not write_state or detect_only:
        return {
            "detection": detection,
            "offer": offer,
            "spawn_disabled": detect_only,
            "wrote_state": False,
        }

    if offer is not None:
        _atomic_write_json(path, offer)
        LOG.info("Wrote unhooked-cluster offer → %s", path)
        return {"detection": detection, "offer": offer, "wrote_state": True}

    # Threshold not met: clear a stale offer so it returns to dormant.
    if path.is_file():
        try:
            path.unlink()
            LOG.info("Cleared stale unhooked-cluster offer (under threshold).")
        except OSError as exc:
            LOG.warning("Could not clear stale offer %s: %s", path, exc)
    return {"detection": detection, "offer": None, "wrote_state": False}

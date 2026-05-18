"""Scan tick — discover jobs, fire triggers, dedup via ledger, gate via governor.

WHY THIS EXISTS
---------------
This is the orchestration core that wires the frozen Wave 1 services
(``ledger``, ``governor``, ``schema``) to the Wave 2 trigger evaluators. It is
**pure orchestration**: it never spawns a subagent/LLM and never re-implements
any admission logic. The anti-duplicate guarantee is delegated entirely to
``Ledger.claim`` (atomic, exactly-one-winner) and the global ceiling to
``Governor.allow`` — exactly the contract in ``SKILL.md`` § "Engine usage
contract".

THE TICK (per the fence spec)
-----------------------------
1. **Discover** every ``proactive:`` block: ``skills/*/SKILL.md`` frontmatter
   OR ``skills/*/proactive.yaml``.
2. ``parse_jobs`` each (frozen Wave 1 parser/validator).
3. For each job: evaluate its trigger → fired candidates.
4. Evaluate the job's ``condition`` against each candidate's context (a small,
   safe predicate language — see :func:`eval_condition`).
5. Render the concrete ``idempotency_key`` from the candidate context
   (``{{...}}`` substitution).
6. ``ledger.should_skip`` (fast path) then ``ledger.claim`` (atomic). Lost
   claim or skip → drop the candidate. **This is the end-to-end
   anti-duplicate point.**
7. ``governor.allow`` — denied → ``ledger.mark(key, "failed")`` and skip+log.
8. Survivors append to the queue.

Hard caps from the engine config block (``SKILL.md`` frontmatter
``engine_config:``, with safe defaults): ``max_spawn_per_tick`` and
``queue_alert_threshold``.

Stdlib only.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # package import
    from .schema import (
        Job,
        parse_jobs,
        SchemaError,
        extract_frontmatter_block,
    )
    from .ledger import Ledger
    from .governor import Governor
    from . import triggers as triggers_mod
    from .triggers import Candidate
except ImportError:  # flat import (engine/ on sys.path — repo's house pattern)
    from schema import (  # type: ignore
        Job,
        parse_jobs,
        SchemaError,
        extract_frontmatter_block,
    )
    from ledger import Ledger  # type: ignore
    from governor import Governor  # type: ignore
    import triggers as triggers_mod  # type: ignore
    from triggers import Candidate  # type: ignore

LOG = logging.getLogger("iga_proactive.runtime")

# Engine-wide hard caps (overridable via engine_config: in iga-proactive
# SKILL.md frontmatter, or the kwargs on scan_tick()).
DEFAULT_MAX_SPAWN_PER_TICK = 3
DEFAULT_QUEUE_ALERT_THRESHOLD = 10

_SKILLS_DIR_DEFAULT = Path(__file__).resolve().parents[3] / "skills"


# --------------------------------------------------------------------------- #
# Outcome model
# --------------------------------------------------------------------------- #
@dataclass
class QueuedCandidate:
    """A candidate that survived condition + claim + governor — ready to
    dispatch. ``idempotency_key`` is the rendered, concrete key the ledger
    holds a live ``claimed`` row for."""

    job: Job
    candidate: Candidate
    idempotency_key: str
    est_tokens: int
    model: str


@dataclass
class TickResult:
    queue: list[QueuedCandidate] = field(default_factory=list)
    discovered_jobs: int = 0
    fired_candidates: int = 0
    condition_skipped: int = 0
    claim_skipped: int = 0  # lost claim OR should_skip (the dedup point)
    governor_denied: int = 0
    queue_alert: bool = False
    errors: list[str] = field(default_factory=list)
    # Sources that are simply NOT proactive skills (no frontmatter, or
    # frontmatter without a `proactive:` key). These are silently skipped —
    # they are NOT errors and never appear in `errors`. Counted only for
    # observability.
    skipped_non_proactive: int = 0


# --------------------------------------------------------------------------- #
# Job discovery
# --------------------------------------------------------------------------- #
def discover_job_sources(skills_dir: Path | str | None = None) -> list[Path]:
    """Return every file that may carry a ``proactive:`` block.

    Looks at ``skills/*/SKILL.md`` and ``skills/*/proactive.yaml``. Order is
    deterministic (sorted) so ticks are reproducible.
    """
    base = Path(skills_dir).expanduser() if skills_dir else _SKILLS_DIR_DEFAULT
    found: list[Path] = []
    if not base.is_dir():
        return found
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            found.append(skill_md)
        pyaml = child / "proactive.yaml"
        if pyaml.is_file():
            found.append(pyaml)
    return found


def _wrap_yaml_as_frontmatter(text: str) -> str:
    """``proactive.yaml`` is a bare YAML doc; the Wave 1 parser expects a
    ``--- ... ---`` frontmatter fence. Wrap it so we reuse one parser."""
    body = text.strip("\n")
    return f"---\n{body}\n---\n"


_PROACTIVE_KEY_RE = re.compile(r"^\s*proactive\s*:\s*(?:\n|$)", re.MULTILINE)


def _has_proactive_block(text: str) -> bool:
    """True iff ``text`` has YAML frontmatter that declares a ``proactive:``
    key. False means: no ``--- ... ---`` fence at all, OR a fence with no
    ``proactive:`` key. Either way the source is simply NOT a proactive skill
    and must be skipped *silently* — never an error.

    A ``proactive:`` key that is present but whose value/list is malformed is
    still detected as present here, so the genuine-malformed path in
    :func:`load_jobs` still emits a real ``errors[]`` entry.
    """
    try:
        fm = extract_frontmatter_block(text)
    except SchemaError:
        # No frontmatter fence → not a proactive skill.
        return False
    return _PROACTIVE_KEY_RE.search(fm) is not None


def load_jobs(sources: list[Path]) -> tuple[list[Job], list[str], int]:
    """Parse every source with the frozen Wave 1 ``parse_jobs``.

    Returns ``(jobs, errors, skipped_non_proactive)``.

    Distinction (the correctness fix):

    * A source with **no frontmatter**, or frontmatter **without a
      ``proactive:`` key**, is simply NOT a proactive skill. It is skipped
      *silently* and counted in ``skipped_non_proactive`` — it never lands in
      ``errors`` (no false red noise in the menu-bar app).
    * A source whose ``proactive:`` block **exists but fails to
      parse/validate** is a genuine malformed job spec → recorded in
      ``errors`` and skipped. One bad skill never aborts the whole tick.
    """
    jobs: list[Job] = []
    errors: list[str] = []
    skipped_non_proactive = 0
    for src in sources:
        try:
            text = src.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{src}: unreadable: {exc}")
            continue
        if src.name == "proactive.yaml":
            text = _wrap_yaml_as_frontmatter(text)
        if not _has_proactive_block(text):
            # Not a proactive skill — silent skip, NOT an error.
            skipped_non_proactive += 1
            continue
        try:
            parsed = parse_jobs(text)
        except SchemaError as exc:
            # A `proactive:` block IS present but is malformed — a real error.
            errors.append(f"{src}: schema error: {exc}")
            continue
        jobs.extend(parsed)
    return jobs, errors, skipped_non_proactive


# --------------------------------------------------------------------------- #
# idempotency-key rendering
# --------------------------------------------------------------------------- #
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def render_template(template: str, ns: dict[str, str]) -> str:
    """Substitute ``{{ a.b }}`` placeholders from ``ns``.

    A missing key renders as the empty string (the candidate still gets a
    deterministic — if coarser — key; the ledger still dedups it). Whitespace
    inside the braces is tolerated.
    """
    def repl(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(ns.get(key, ""))

    return _PLACEHOLDER_RE.sub(repl, template)


# --------------------------------------------------------------------------- #
# condition evaluation — tiny, safe predicate language
# --------------------------------------------------------------------------- #
_COND_OPS = ("==", "!=", " contains ", " in ", " exists", " not exists")


def eval_condition(condition: str | None, ns: dict[str, str]) -> bool:
    """Evaluate a job ``condition`` against a candidate namespace.

    Deliberately tiny and ``eval``-free (never run arbitrary code from a
    SKILL.md). Supported forms (case-sensitive keys, whitespace-tolerant):

      * ``None`` / empty                     → True (no gating)
      * ``manual``                           → True (always-eligible marker)
      * ``<key> exists``                     → key present & non-empty
      * ``<key> not exists`` / ``not exists drawer for task``
                                             → conservatively True here;
        real existence checks are the worker/trigger's job (the old scanner
        deduped against MemPalace, not in a SKILL.md predicate). We do NOT
        silently treat it as a hard gate the engine can't actually evaluate.
      * ``<key> == <value>`` / ``!=``        → string compare against ns
      * ``<key> contains <substr>``          → substring test
      * ``<value> in <key>``                 → membership in ns[key]

    Anything we cannot parse → True with a logged warning (fail-open is the
    safe default: the ledger + governor still gate the actual spawn, so a
    mis-written condition can't cause a duplicate or a budget breach — it can
    only let a candidate through to those real guards).
    """
    if condition is None:
        return True
    c = condition.strip()
    if not c or c == "manual":
        return True

    if c.endswith(" not exists") or c.startswith("not exists"):
        # Cannot truly evaluate "no drawer exists for this task" from a flat
        # namespace — that requires a store query the trigger layer already
        # owns. Fail-open: defer to ledger/governor (the real guards).
        return True

    if c.endswith(" exists"):
        key = c[: -len(" exists")].strip()
        return bool(ns.get(key, "").strip())

    if " contains " in c:
        key, _, needle = c.partition(" contains ")
        return needle.strip() in ns.get(key.strip(), "")

    if " in " in c and "==" not in c and "!=" not in c:
        val, _, key = c.partition(" in ")
        return val.strip() in ns.get(key.strip(), "")

    if "==" in c:
        key, _, val = c.partition("==")
        return ns.get(key.strip(), "") == val.strip().strip("'\"")

    if "!=" in c:
        key, _, val = c.partition("!=")
        return ns.get(key.strip(), "") != val.strip().strip("'\"")

    LOG.warning(
        "Unparseable condition %r — failing open (ledger/governor still gate)",
        condition,
    )
    return True


# --------------------------------------------------------------------------- #
# budget extraction
# --------------------------------------------------------------------------- #
_DEFAULT_MODEL = "claude-opus-4-7[1m]"
_DEFAULT_EST_TOKENS = 200_000


def _budget_model(job: Job) -> str:
    m = job.budget.get("model")
    return str(m) if m else _DEFAULT_MODEL


def _budget_est_tokens(job: Job) -> int:
    """Estimate tokens for governor accounting. Explicit ``budget.est_tokens``
    wins; else derive from ``budget.wall_min`` (rough: 100k tokens / 10 wall
    min of an Opus research run); else a conservative default."""
    et = job.budget.get("est_tokens")
    if isinstance(et, int) and et >= 0:
        return et
    wall = job.budget.get("wall_min")
    if isinstance(wall, int) and wall > 0:
        return wall * 10_000
    return _DEFAULT_EST_TOKENS


# --------------------------------------------------------------------------- #
# engine config (caps) from iga-proactive SKILL.md frontmatter
# --------------------------------------------------------------------------- #
def _read_engine_caps(skills_dir: Path) -> tuple[int, int]:
    """Best-effort read of ``engine_config: { max_spawn_per_tick, ... }`` from
    this skill's own SKILL.md. Missing/unparseable → safe defaults. Never
    raises — caps default conservatively."""
    max_spawn = DEFAULT_MAX_SPAWN_PER_TICK
    alert = DEFAULT_QUEUE_ALERT_THRESHOLD
    skill_md = skills_dir / "iga-proactive" / "SKILL.md"
    if not skill_md.is_file():
        return max_spawn, alert
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return max_spawn, alert
    m = re.search(r"max_spawn_per_tick\s*:\s*(\d+)", text)
    if m:
        max_spawn = int(m.group(1))
    m = re.search(r"queue_alert_threshold\s*:\s*(\d+)", text)
    if m:
        alert = int(m.group(1))
    # Env override (operational kill-dial without editing the skill).
    env_cap = os.environ.get("IGA_MAX_SPAWN_PER_TICK")
    if env_cap is not None:
        try:
            max_spawn = max(0, int(env_cap))
        except ValueError:
            LOG.warning("Invalid IGA_MAX_SPAWN_PER_TICK=%r, ignoring", env_cap)
    return max_spawn, alert


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #
def scan_tick(
    *,
    now: datetime | None = None,
    skills_dir: Path | str | None = None,
    ledger: Ledger | None = None,
    governor: Governor | None = None,
    db_path: str | os.PathLike | None = None,
    token: str | None = None,
    todoist_fetcher: Callable[[str, str], list[dict[str, Any]]] | None = None,
    mempalace_mod: Any | None = None,
    max_spawn_per_tick: int | None = None,
    queue_alert_threshold: int | None = None,
) -> TickResult:
    """Run one scan tick. Pure orchestration — spawns nothing.

    Everything external is injectable so the whole tick is unit-testable with
    no network, no MCP, and a temp-file ledger/governor db.
    """
    now = now or datetime.now(timezone.utc)
    base = Path(skills_dir).expanduser() if skills_dir else _SKILLS_DIR_DEFAULT
    ledger = ledger or Ledger(db_path)
    governor = governor or Governor(db_path)

    caps_spawn, caps_alert = _read_engine_caps(base.parent / "skills" if base.name != "skills" else base)
    if max_spawn_per_tick is None:
        max_spawn_per_tick = caps_spawn
    if queue_alert_threshold is None:
        queue_alert_threshold = caps_alert

    res = TickResult()

    sources = discover_job_sources(base)
    jobs, load_errs, skipped_np = load_jobs(sources)
    res.discovered_jobs = len(jobs)
    res.errors.extend(load_errs)
    res.skipped_non_proactive = skipped_np

    for job in jobs:
        try:
            candidates = triggers_mod.evaluate(
                job.trigger,
                now=now,
                token=token,
                todoist_fetcher=todoist_fetcher,
                mempalace_mod=mempalace_mod,
            )
        except NotImplementedError as exc:
            # WAVE3 stub trigger (calendar/watch). One unimplemented trigger
            # must not abort the whole tick.
            res.errors.append(f"{job.id}: trigger not implemented: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — a bad job can't kill the tick
            res.errors.append(f"{job.id}: trigger raised: {exc}")
            continue

        for cand in candidates:
            res.fired_candidates += 1
            ns = cand.render_context()

            if not eval_condition(job.condition, ns):
                res.condition_skipped += 1
                continue

            key = render_template(job.idempotency_key, ns)

            # --- THE anti-duplicate point (delegated to frozen Wave 1) ---
            if ledger.should_skip(key):
                res.claim_skipped += 1
                continue
            if not ledger.claim(key, job.id, job.cooldown_seconds):
                # Lost the atomic race / live row exists. Exactly-one-winner.
                res.claim_skipped += 1
                continue

            model = _budget_model(job)
            est = _budget_est_tokens(job)
            decision = governor.allow(model, est)
            if not decision.ok:
                # Release nothing — mark failed so the cooldown still holds
                # (we do NOT want a denied job to instantly retry-storm).
                ledger.mark(key, "failed")
                res.governor_denied += 1
                LOG.info("Governor denied %s (%s): %s", job.id, key, decision.reason)
                continue

            res.queue.append(
                QueuedCandidate(
                    job=job,
                    candidate=cand,
                    idempotency_key=key,
                    est_tokens=est,
                    model=model,
                )
            )

    if len(res.queue) > queue_alert_threshold:
        res.queue_alert = True
        LOG.warning(
            "Queue length %d exceeds alert threshold %d",
            len(res.queue),
            queue_alert_threshold,
        )

    # Hard cap: trim to max_spawn_per_tick. Trimmed candidates already hold a
    # ledger 'claimed' row, so they will NOT re-spawn on the next tick within
    # cooldown — they are simply deferred (their cooldown window protects
    # them; the next tick sees should_skip()=True). This is intentional: the
    # cap throttles spawn rate without losing the dedup guarantee.
    if max_spawn_per_tick >= 0 and len(res.queue) > max_spawn_per_tick:
        res.queue = res.queue[:max_spawn_per_tick]

    return res

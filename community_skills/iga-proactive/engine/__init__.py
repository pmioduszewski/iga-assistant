"""Iga proactive engine — Wave 1 (foundation/correctness core).

Wave 1 ships two runtime services that make the prior bespoke system's
duplicate-spawn failure structurally impossible:

  - ledger:   exact-match sqlite idempotency + cooldown ledger with an
              ATOMIC claim (replaces semantic "did I do this?" guessing).
  - governor: a single global budget accountant above ALL jobs with a
              rolling-window circuit breaker.

Plus the job-schema parser/validator for the SKILL.md ``proactive:`` block.

Out of scope for Wave 1 (later waves): runtime scanner, dispatcher, worker
spawning, research-job port, surfacer, launchd/daemon, menu-bar app.
"""

from .ledger import Ledger
from .governor import Governor, Decision
from .schema import Job, parse_jobs, validate, parse_duration_to_seconds

__all__ = [
    "Ledger",
    "Governor",
    "Decision",
    "Job",
    "parse_jobs",
    "validate",
    "parse_duration_to_seconds",
]

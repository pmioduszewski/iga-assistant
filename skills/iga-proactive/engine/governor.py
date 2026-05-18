"""Global budget governor — the single accountant above ALL proactive jobs.

WHY THIS EXISTS
---------------
The prior bespoke system had no global ceiling: per-topic logic spawned
workers independently, so 4 duplicate workers for ONE topic burned ~70% of a
5-hour quota window before anything noticed. The governor is a single
accountant: every dispatcher MUST call :meth:`Governor.allow` BEFORE spawning
a worker, and call :meth:`Governor.record` AFTER a successful spawn. No job
gets to reason about budget locally — there is exactly one ceiling.

MODEL
-----
Backed by the shared ``dispatch_log`` table (same db as the ledger). Three
rolling windows are enforced:

  * ``max_invocations_5h``  — invocations in the trailing 5 hours
  * ``max_invocations_24h`` — invocations in the trailing 24 hours
  * ``max_est_tokens_5h``   — summed ``est_tokens`` in the trailing 5 hours

CIRCUIT BREAKER
---------------
If any ceiling is reached/exceeded, :meth:`allow` returns ``ok=False`` with a
specific reason. The breaker is *windowed*: it does not reset on a timer — it
stays tripped for as long as the offending window remains saturated, and
clears automatically once enough old dispatches roll out of the window. There
is no manual reset; the window IS the reset.

Stdlib only (``sqlite3``).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:  # package import (skills.iga-proactive.engine.*)
    from .ledger import default_db_path, _SCHEMA  # reuse the same schema/db
except ImportError:  # flat import (engine/ on sys.path, repo's house pattern)
    from ledger import default_db_path, _SCHEMA


@dataclass(frozen=True)
class Decision:
    """Result of :meth:`Governor.allow`."""

    ok: bool
    reason: str


# Defaults (tunable via constructor args / SKILL.md config block).
DEFAULT_MAX_INVOCATIONS_5H = 8
DEFAULT_MAX_INVOCATIONS_24H = 20
DEFAULT_MAX_EST_TOKENS_5H = 2_000_000

_WINDOW_5H = timedelta(hours=5)
_WINDOW_24H = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Governor:
    """Single global budget accountant. One instance gates every dispatch.

    Usage contract (enforced by convention; later waves wire this in):

        gov = Governor()
        d = gov.allow(model, est_tokens)
        if not d.ok:
            skip(d.reason)          # do NOT spawn
        else:
            spawn_worker(...)       # actually spawn
            gov.record(model, est_tokens)   # record AFTER success only
    """

    def __init__(
        self,
        db_path: str | os.PathLike | None = None,
        *,
        max_invocations_5h: int = DEFAULT_MAX_INVOCATIONS_5H,
        max_invocations_24h: int = DEFAULT_MAX_INVOCATIONS_24H,
        max_est_tokens_5h: int = DEFAULT_MAX_EST_TOKENS_5H,
        job_id: str = "_governor",
    ):
        self.db_path = Path(db_path).expanduser() if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_invocations_5h = max_invocations_5h
        self.max_invocations_24h = max_invocations_24h
        self.max_est_tokens_5h = max_est_tokens_5h
        self._job_id = job_id
        self._init_schema()

    # ----------------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path), timeout=30.0, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _window_stats(
        self, conn: sqlite3.Connection, now: datetime
    ) -> tuple[int, int, int]:
        """Return (count_5h, count_24h, est_tokens_5h) from dispatch_log."""
        cutoff_5h = _iso(now - _WINDOW_5H)
        cutoff_24h = _iso(now - _WINDOW_24H)
        row = conn.execute(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN ts > ? THEN 1 ELSE 0 END), 0) AS c5, "
            "  COALESCE(SUM(CASE WHEN ts > ? THEN 1 ELSE 0 END), 0) AS c24, "
            "  COALESCE(SUM(CASE WHEN ts > ? THEN est_tokens ELSE 0 END), 0) AS t5 "
            "FROM dispatch_log;",
            (cutoff_5h, cutoff_24h, cutoff_5h),
        ).fetchone()
        return int(row["c5"]), int(row["c24"]), int(row["t5"])

    # ----------------------------------------------------------------- #
    # public API
    # ----------------------------------------------------------------- #
    def allow(self, model: str, est_tokens: int) -> Decision:
        """Decide whether a new dispatch is permitted RIGHT NOW.

        Returns ``Decision(ok=False, reason=...)`` if admitting this dispatch
        would meet or exceed any ceiling, or if a ceiling is already
        saturated (circuit breaker tripped). MUST be called before every
        spawn. Does NOT mutate state — call :meth:`record` after a real spawn.
        """
        if est_tokens < 0:
            return Decision(False, "est_tokens must be >= 0")

        conn = self._connect()
        try:
            now = _utcnow()
            c5, c24, t5 = self._window_stats(conn, now)
        finally:
            conn.close()

        # Breaker: already-saturated windows block immediately.
        if c5 >= self.max_invocations_5h:
            return Decision(
                False,
                f"5h invocation ceiling reached "
                f"({c5}/{self.max_invocations_5h}) — breaker tripped, "
                f"waiting for window to roll",
            )
        if c24 >= self.max_invocations_24h:
            return Decision(
                False,
                f"24h invocation ceiling reached "
                f"({c24}/{self.max_invocations_24h}) — breaker tripped, "
                f"waiting for window to roll",
            )
        if t5 >= self.max_est_tokens_5h:
            return Decision(
                False,
                f"5h est-token ceiling reached "
                f"({t5}/{self.max_est_tokens_5h}) — breaker tripped, "
                f"waiting for window to roll",
            )

        # Admitting this one must not push a window over its ceiling.
        if c5 + 1 > self.max_invocations_5h:
            return Decision(
                False,
                f"would exceed 5h invocation ceiling "
                f"({c5}+1 > {self.max_invocations_5h})",
            )
        if c24 + 1 > self.max_invocations_24h:
            return Decision(
                False,
                f"would exceed 24h invocation ceiling "
                f"({c24}+1 > {self.max_invocations_24h})",
            )
        if t5 + est_tokens > self.max_est_tokens_5h:
            return Decision(
                False,
                f"would exceed 5h est-token ceiling "
                f"({t5}+{est_tokens} > {self.max_est_tokens_5h})",
            )

        return Decision(True, "within budget")

    def record(self, model: str, est_tokens: int, job_id: str | None = None) -> None:
        """Append a dispatch to ``dispatch_log``. Call AFTER a successful
        spawn only — recording a spawn that never happened poisons the
        windows and falsely trips the breaker."""
        if est_tokens < 0:
            raise ValueError("est_tokens must be >= 0")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO dispatch_log (ts, job_id, model, est_tokens) "
                "VALUES (?, ?, ?, ?);",
                (_iso(_utcnow()), job_id or self._job_id, model, int(est_tokens)),
            )
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def stats(self) -> dict:
        """Current window utilisation (for diagnostics / a future meter)."""
        conn = self._connect()
        try:
            now = _utcnow()
            c5, c24, t5 = self._window_stats(conn, now)
        finally:
            conn.close()
        return {
            "invocations_5h": c5,
            "max_invocations_5h": self.max_invocations_5h,
            "invocations_24h": c24,
            "max_invocations_24h": self.max_invocations_24h,
            "est_tokens_5h": t5,
            "max_est_tokens_5h": self.max_est_tokens_5h,
        }

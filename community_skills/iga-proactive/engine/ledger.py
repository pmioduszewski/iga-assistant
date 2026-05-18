"""Exact-match sqlite idempotency + cooldown ledger.

WHY THIS EXISTS
---------------
The prior bespoke proactive system used a *semantic vector search* to answer
"did I already do this?" — which cannot do exact idempotency. The result: it
spawned **4 duplicate background workers for one topic** and burned ~70% of a
5-hour quota window. This ledger replaces that fuzzy check with an EXACT
primary-key claim performed inside a single serialized sqlite transaction.

The anti-duplicate core is :meth:`Ledger.claim`. Under concurrent callers for
the same ``idempotency_key`` against the same db, EXACTLY ONE returns True.
This is enforced by ``BEGIN IMMEDIATE`` (acquires the reserved write lock so
only one writer evaluates the predicate at a time) plus the PRIMARY KEY on
``idempotency_key``.

Stdlib only (``sqlite3``).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

_LIVE_STATUSES = ("claimed", "running")
_VALID_STATUSES = ("claimed", "running", "done", "failed", "timeout")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    idempotency_key TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    last_run_ts     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK(status IN
                        ('claimed','running','done','failed','timeout')),
    output_ref      TEXT,
    cooldown_until  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    model       TEXT NOT NULL,
    est_tokens  INTEGER NOT NULL DEFAULT 0
);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def default_db_path() -> Path:
    """``$IGA_PROACTIVE_DB`` if set, else ``~/Gaia/state/proactive.db``."""
    env = os.environ.get("IGA_PROACTIVE_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Iga" / "state" / "proactive.db"


class Ledger:
    """SQLite-backed idempotency + cooldown ledger.

    A fresh :class:`sqlite3.Connection` is opened per operation so the ledger
    is safe to share across threads/processes (sqlite handles the file lock).
    """

    def __init__(self, db_path: str | os.PathLike | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ----------------------------------------------------------------- #
    # connection / schema
    # ----------------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,            # wait on the write lock instead of failing fast
            isolation_level=None,    # explicit transaction control (BEGIN IMMEDIATE)
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    # ----------------------------------------------------------------- #
    # core API
    # ----------------------------------------------------------------- #
    def claim(
        self,
        idempotency_key: str,
        job_id: str,
        cooldown_seconds: int,
    ) -> bool:
        """Atomically claim a run for ``idempotency_key``.

        Returns True (and writes a row with ``status='claimed'``,
        ``cooldown_until = now + cooldown_seconds``) ONLY if no live row
        exists for the key — i.e. there is no row, OR the existing row's
        cooldown has elapsed AND its status is not one of
        ``('claimed','running')``.

        Concurrent callers for the same key: EXACTLY ONE returns True. The
        whole read-decide-write is done inside one ``BEGIN IMMEDIATE``
        transaction, so writers are serialized by sqlite's reserved lock and
        the predicate is never evaluated against stale state.
        """
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            now = _utcnow()
            row = conn.execute(
                "SELECT status, cooldown_until FROM job_runs "
                "WHERE idempotency_key = ?;",
                (idempotency_key,),
            ).fetchone()

            if row is not None:
                live = row["status"] in _LIVE_STATUSES
                cooled = _parse_iso(row["cooldown_until"]) > now
                if live or cooled:
                    conn.execute("ROLLBACK;")
                    return False

            cooldown_until = now + timedelta(seconds=cooldown_seconds)
            # Upsert: replace a stale (expired, terminal) row for the key.
            conn.execute(
                "INSERT INTO job_runs "
                "(idempotency_key, job_id, last_run_ts, status, "
                " output_ref, cooldown_until) "
                "VALUES (?, ?, ?, 'claimed', NULL, ?) "
                "ON CONFLICT(idempotency_key) DO UPDATE SET "
                "  job_id=excluded.job_id, "
                "  last_run_ts=excluded.last_run_ts, "
                "  status='claimed', "
                "  output_ref=NULL, "
                "  cooldown_until=excluded.cooldown_until;",
                (idempotency_key, job_id, _iso(now), _iso(cooldown_until)),
            )
            conn.execute("COMMIT;")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def mark(
        self,
        idempotency_key: str,
        status: str,
        output_ref: str | None = None,
    ) -> None:
        """Transition a previously-claimed run to a new status."""
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; expected one of {_VALID_STATUSES}"
            )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE job_runs SET status = ?, output_ref = ?, "
                "last_run_ts = ? WHERE idempotency_key = ?;",
                (status, output_ref, _iso(_utcnow()), idempotency_key),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK;")
                raise KeyError(
                    f"cannot mark unknown idempotency_key: {idempotency_key!r}"
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

    def should_skip(self, idempotency_key: str) -> bool:
        """True if a live row exists: still within cooldown, OR active
        (status in ``claimed``/``running``)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status, cooldown_until FROM job_runs "
                "WHERE idempotency_key = ?;",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                return False
            if row["status"] in _LIVE_STATUSES:
                return True
            return _parse_iso(row["cooldown_until"]) > _utcnow()
        finally:
            conn.close()

    # ----------------------------------------------------------------- #
    # dispatch_log helpers (shared table; governor reads/writes it too)
    # ----------------------------------------------------------------- #
    def log_dispatch(self, job_id: str, model: str, est_tokens: int = 0) -> None:
        """Append a row to ``dispatch_log`` (audit of an actual spawn)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "INSERT INTO dispatch_log (ts, job_id, model, est_tokens) "
                "VALUES (?, ?, ?, ?);",
                (_iso(_utcnow()), job_id, model, int(est_tokens)),
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

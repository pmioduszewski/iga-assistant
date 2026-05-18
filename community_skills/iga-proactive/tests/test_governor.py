"""Governor tests — ceilings, window roll-off, breaker stickiness."""

import sys
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from governor import Governor  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "proactive.db"


def _backdate(db_path, minutes_ago: float, n: int = 1, est_tokens: int = 0):
    """Insert n dispatch_log rows timestamped `minutes_ago` minutes in the
    past, to simulate prior activity / window positioning."""
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO dispatch_log (ts, job_id, model, est_tokens) "
            "VALUES (?, 'j', 'm', ?);",
            [(ts, est_tokens)] * n,
        )
        conn.commit()
    finally:
        conn.close()


def test_allows_when_empty(db_path):
    gov = Governor(db_path)
    assert gov.allow("opus", 1000).ok is True


def test_record_then_allow_interplay(db_path):
    gov = Governor(db_path, max_invocations_5h=2)
    assert gov.allow("opus", 0).ok is True
    gov.record("opus", 0)
    assert gov.allow("opus", 0).ok is True
    gov.record("opus", 0)
    d = gov.allow("opus", 0)
    assert d.ok is False
    assert "5h invocation ceiling" in d.reason


def test_5h_invocation_ceiling(db_path):
    gov = Governor(db_path, max_invocations_5h=3)
    _backdate(db_path, minutes_ago=10, n=3)
    d = gov.allow("opus", 0)
    assert d.ok is False
    assert "5h invocation ceiling reached" in d.reason


def test_24h_invocation_ceiling(db_path):
    gov = Governor(db_path, max_invocations_5h=999, max_invocations_24h=5)
    # 5 rows older than 5h but within 24h -> 24h ceiling, not 5h.
    _backdate(db_path, minutes_ago=6 * 60, n=5)
    d = gov.allow("opus", 0)
    assert d.ok is False
    assert "24h invocation ceiling reached" in d.reason


def test_est_token_ceiling(db_path):
    gov = Governor(db_path, max_est_tokens_5h=1_000_000)
    _backdate(db_path, minutes_ago=10, n=1, est_tokens=900_000)
    # Under ceiling now, but this request would push over.
    d = gov.allow("opus", 200_000)
    assert d.ok is False
    assert "5h est-token ceiling" in d.reason


def test_window_roll_off_clears_breaker(db_path):
    gov = Governor(db_path, max_invocations_5h=2)
    # 2 dispatches just over 5h ago -> outside the 5h window entirely.
    _backdate(db_path, minutes_ago=5 * 60 + 1, n=2)
    # They no longer count against the 5h window -> allowed again.
    assert gov.allow("opus", 0).ok is True


def test_breaker_stays_tripped_within_window(db_path):
    gov = Governor(db_path, max_invocations_5h=2)
    _backdate(db_path, minutes_ago=1, n=2)
    # Repeated calls within the window stay blocked (no auto-untrip on retry).
    for _ in range(5):
        assert gov.allow("opus", 0).ok is False


def test_negative_est_tokens_rejected(db_path):
    gov = Governor(db_path)
    assert gov.allow("opus", -1).ok is False
    with pytest.raises(ValueError):
        gov.record("opus", -1)


def test_stats_report(db_path):
    gov = Governor(db_path, max_invocations_5h=4)
    gov.record("opus", 1234)
    s = gov.stats()
    assert s["invocations_5h"] == 1
    assert s["est_tokens_5h"] == 1234
    assert s["max_invocations_5h"] == 4

"""Ledger tests — including the mandatory 4-concurrent-claim regression.

The regression test reproduces the literal production failure: 4 workers
racing to claim the SAME idempotency key. Exactly ONE must win.
"""

import sys
import threading
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from ledger import Ledger  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "proactive.db"


def test_first_claim_succeeds(db_path):
    led = Ledger(db_path)
    assert led.claim("k1", "job-a", cooldown_seconds=3600) is True


def test_second_claim_within_cooldown_fails(db_path):
    led = Ledger(db_path)
    assert led.claim("k1", "job-a", 3600) is True
    assert led.claim("k1", "job-a", 3600) is False


def test_should_skip_reflects_live_row(db_path):
    led = Ledger(db_path)
    assert led.should_skip("k1") is False
    led.claim("k1", "job-a", 3600)
    assert led.should_skip("k1") is True


def test_mark_transitions_status(db_path):
    led = Ledger(db_path)
    led.claim("k1", "job-a", 3600)
    led.mark("k1", "done", output_ref="drawer://abc")
    # Still within cooldown so should_skip stays True even though done.
    assert led.should_skip("k1") is True


def test_mark_unknown_key_raises(db_path):
    led = Ledger(db_path)
    with pytest.raises(KeyError):
        led.mark("nope", "done")


def test_mark_invalid_status_raises(db_path):
    led = Ledger(db_path)
    led.claim("k1", "job-a", 3600)
    with pytest.raises(ValueError):
        led.mark("k1", "bogus")


def test_expired_cooldown_terminal_status_allows_reclaim(db_path):
    led = Ledger(db_path)
    # cooldown 0 => cooldown_until == now, not strictly in the future.
    assert led.claim("k1", "job-a", cooldown_seconds=0) is True
    led.mark("k1", "done")
    # Cooldown elapsed AND status terminal => reclaimable.
    assert led.should_skip("k1") is False
    assert led.claim("k1", "job-a", cooldown_seconds=3600) is True


def test_expired_cooldown_but_still_claimed_blocks_reclaim(db_path):
    """Active (claimed/running) row must block even past cooldown — a stuck
    worker should not be double-spawned just because time passed."""
    led = Ledger(db_path)
    assert led.claim("k1", "job-a", cooldown_seconds=0) is True
    # status still 'claimed', cooldown elapsed -> live because active.
    assert led.should_skip("k1") is True
    assert led.claim("k1", "job-a", cooldown_seconds=3600) is False


# --------------------------------------------------------------------------- #
# THE REGRESSION TEST — 4 concurrent claims, exactly one winner.
# This is the literal production bug (4 duplicate workers / one topic).
# --------------------------------------------------------------------------- #
def test_four_concurrent_claims_exactly_one_winner(db_path):
    N = 4
    # Ensure schema exists before the race so all threads hit a ready db.
    Ledger(db_path)

    results: list[bool] = []
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(N)

    def worker():
        # Each thread uses its OWN Ledger (own connections) against the
        # SAME db file — the realistic concurrency shape.
        led = Ledger(db_path)
        start_barrier.wait()  # maximize contention: all fire together
        won = led.claim("SAME-KEY", "topic-x", cooldown_seconds=3600)
        with results_lock:
            results.append(won)

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == N, f"expected {N} results, got {len(results)}"
    # The load-bearing assertion:
    assert results.count(True) == 1, (
        f"expected EXACTLY ONE winner, got {results.count(True)} "
        f"(results={results})"
    )
    assert results.count(False) == N - 1


# --------------------------------------------------------------------------- #
# user-cancel (sticky terminal status) — Iga UI "Cancel" button
# --------------------------------------------------------------------------- #
def test_cancel_makes_should_skip_true_forever(db_path):
    led = Ledger(db_path)
    led.claim("c1", "job-a", cooldown_seconds=3600)
    led.cancel("c1")
    # Sticky regardless of cooldown elapsing.
    assert led.should_skip("c1") is True


def test_cancelled_key_is_never_reclaimed_even_after_cooldown(db_path):
    led = Ledger(db_path)
    # cooldown 0 → would normally be immediately re-claimable.
    led.claim("c2", "job-a", cooldown_seconds=0)
    led.cancel("c2")
    assert led.claim("c2", "job-a", cooldown_seconds=0) is False
    assert led.should_skip("c2") is True


def test_cancel_works_without_prior_claim(db_path):
    led = Ledger(db_path)
    led.cancel("never-claimed")  # upsert, not mark — must not raise
    assert led.should_skip("never-claimed") is True
    assert led.claim("never-claimed", "job-a", cooldown_seconds=0) is False


def test_cancel_is_idempotent(db_path):
    led = Ledger(db_path)
    led.claim("c3", "job-a", cooldown_seconds=10)
    led.cancel("c3")
    led.cancel("c3")  # no-op, must not raise
    assert led.should_skip("c3") is True


def test_legacy_db_without_cancelled_check_is_migrated(tmp_path):
    """A DB created with the pre-'cancelled' CHECK must be transparently
    migrated so cancel() doesn't hit a CHECK violation."""
    import sqlite3

    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE job_runs ("
        " idempotency_key TEXT PRIMARY KEY,"
        " job_id TEXT NOT NULL,"
        " last_run_ts TEXT NOT NULL,"
        " status TEXT NOT NULL CHECK(status IN"
        "   ('claimed','running','done','failed','timeout')),"
        " output_ref TEXT,"
        " cooldown_until TEXT NOT NULL"
        ");"
    )
    conn.commit()
    conn.close()

    led = Ledger(p)  # __init__ runs the idempotent migration
    led.claim("legacy-k", "job-a", cooldown_seconds=10)
    led.cancel("legacy-k")  # would raise IntegrityError pre-migration
    assert led.should_skip("legacy-k") is True

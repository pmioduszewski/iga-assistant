"""Unit tests for the habit-tracker widget producer.

Covers: grid math (streak, level bucketing, date window), v1 schema
correctness, and the empty-log graceful path. Pure + deterministic — no I/O
except the atomic-write round-trip tests which use tmp_path.

DATA-LOSS ISOLATION (binding): every test that runs the producer's I/O
(``produce``/``main``) MUST point ``$IGA_STATE_DIR`` at a pytest
``tmp_path`` and assert it wrote THERE. No test may read or write the
user's real ``~/Gaia/state`` tree. ``test_isolation_guard_*`` proves the
real widget JSON is byte- and mtime-untouched across producer runs.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

# Load the producer module by path (engine/ is not a package on sys.path).
_PRODUCER = (
    Path(__file__).resolve().parents[1] / "engine" / "producer.py"
)
_spec = importlib.util.spec_from_file_location("ht_producer", _PRODUCER)
producer = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(producer)  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# log parsing
# --------------------------------------------------------------------------- #
def test_parse_log_tolerates_blanks_dups_and_garbage():
    text = "2026-05-01\n\n2026-05-01\n  2026-05-02 \n# a comment\nnonsense\n"
    got = producer.parse_log(text)
    assert got == {date(2026, 5, 1), date(2026, 5, 2)}


def test_parse_log_empty():
    assert producer.parse_log("") == set()
    assert producer.parse_log("\n\n  \n") == set()


# --------------------------------------------------------------------------- #
# streak math
# --------------------------------------------------------------------------- #
def test_current_streak_counts_consecutive_ending_today():
    today = date(2026, 5, 16)
    done = {today, today - timedelta(days=1), today - timedelta(days=2)}
    assert producer.current_streak(done, today=today) == 3


def test_current_streak_allows_yesterday_anchor():
    # Today not logged yet, but yesterday + before are — streak not broken.
    today = date(2026, 5, 16)
    done = {today - timedelta(days=1), today - timedelta(days=2)}
    assert producer.current_streak(done, today=today) == 2


def test_current_streak_broken_when_gap():
    today = date(2026, 5, 16)
    done = {today - timedelta(days=3), today - timedelta(days=4)}
    assert producer.current_streak(done, today=today) == 0


def test_current_streak_empty():
    assert producer.current_streak(set(), today=date(2026, 5, 16)) == 0


def test_days_since_last():
    today = date(2026, 5, 16)
    assert producer.days_since_last({today}, today=today) == 0
    assert (
        producer.days_since_last(
            {today - timedelta(days=4)}, today=today
        )
        == 4
    )
    assert producer.days_since_last(set(), today=today) is None


# --------------------------------------------------------------------------- #
# level bucketing
# --------------------------------------------------------------------------- #
def test_level_zero_when_not_done():
    today = date(2026, 5, 16)
    assert producer._level_for_day(today, set()) == 0


def test_level_isolated_day_is_dimmer_than_sustained_streak():
    today = date(2026, 5, 16)
    isolated = {today}
    sustained = {today - timedelta(days=i) for i in range(7)}
    lo = producer._level_for_day(today, isolated)
    hi = producer._level_for_day(today, sustained)
    assert 1 <= lo <= producer.LEVELS
    assert hi == producer.LEVELS
    assert lo < hi


def test_level_never_exceeds_levels_and_min_one_when_done():
    today = date(2026, 5, 16)
    done = {today - timedelta(days=i) for i in range(30)}
    for off in range(10):
        d = today - timedelta(days=off)
        lvl = producer._level_for_day(d, done)
        assert 1 <= lvl <= producer.LEVELS


# --------------------------------------------------------------------------- #
# date window / cells
# --------------------------------------------------------------------------- #
def test_build_cells_window_length_and_order():
    today = date(2026, 5, 16)
    cells = producer.build_cells(set(), today=today, window_days=120)
    assert len(cells) == 120
    # oldest first, newest last, contiguous daily
    assert cells[0]["date"] == (today - timedelta(days=119)).isoformat()
    assert cells[-1]["date"] == today.isoformat()
    parsed = [date.fromisoformat(c["date"]) for c in cells]
    assert parsed == sorted(parsed)
    assert all(
        (parsed[i + 1] - parsed[i]).days == 1
        for i in range(len(parsed) - 1)
    )


def test_build_cells_levels_reflect_done_dates():
    today = date(2026, 5, 16)
    done = {today, today - timedelta(days=1)}
    cells = producer.build_cells(done, today=today, window_days=10)
    by_date = {c["date"]: c["level"] for c in cells}
    assert by_date[today.isoformat()] >= 1
    assert by_date[(today - timedelta(days=1)).isoformat()] >= 1
    assert by_date[(today - timedelta(days=5)).isoformat()] == 0


# --------------------------------------------------------------------------- #
# coach line (deterministic)
# --------------------------------------------------------------------------- #
def test_coach_empty_is_nudge():
    c = producer.coach_line(set(), today=date(2026, 5, 16), window_days=120)
    assert c is not None and c["tone"] == "nudge"
    assert "logged" in c["text"].lower()


def test_coach_streak_is_encouraging():
    today = date(2026, 5, 16)
    done = {today - timedelta(days=i) for i in range(5)}
    c = producer.coach_line(done, today=today, window_days=120)
    assert c is not None and c["tone"] == "encouraging"
    assert "streak" in c["text"].lower()


def test_coach_long_gap_is_nudge():
    today = date(2026, 5, 16)
    done = {today - timedelta(days=20)}
    c = producer.coach_line(done, today=today, window_days=120)
    assert c is not None and c["tone"] == "nudge"


def test_coach_is_deterministic():
    today = date(2026, 5, 16)
    done = {today - timedelta(days=i) for i in range(3)}
    a = producer.coach_line(done, today=today, window_days=120)
    b = producer.coach_line(done, today=today, window_days=120)
    assert a == b


# --------------------------------------------------------------------------- #
# schema correctness
# --------------------------------------------------------------------------- #
def test_build_widget_data_schema_shape():
    today = date(2026, 5, 16)
    done = {today, today - timedelta(days=1)}
    payload = producer.build_widget_data(
        "reading", today=today, window_days=120, done=done
    )
    assert payload["schema_version"] == 1
    assert payload["widget_id"] == "habit-grid"
    assert payload["type"] == "contribution-grid"
    assert payload["title"] == "Habit streak"
    assert isinstance(payload["generated_at"], str)
    d = payload["data"]
    assert set(d.keys()) == {"label", "levels", "cells"}
    assert d["levels"] == producer.LEVELS
    assert len(d["cells"]) == 120
    assert "reading" in d["label"]
    for cell in d["cells"]:
        assert set(cell.keys()) == {"date", "level"}
        assert 0 <= cell["level"] <= d["levels"]
        date.fromisoformat(cell["date"])  # parses
    assert payload["coach"] is not None
    assert set(payload["coach"].keys()) == {"text", "tone"}


def test_build_widget_data_empty_log_graceful():
    today = date(2026, 5, 16)
    payload = producer.build_widget_data(
        "nope", today=today, window_days=30, done=set()
    )
    assert payload["schema_version"] == 1
    assert len(payload["data"]["cells"]) == 30
    assert all(c["level"] == 0 for c in payload["data"]["cells"])
    assert payload["coach"]["tone"] == "nudge"
    # JSON-serialisable
    json.dumps(payload)


def test_produce_writes_atomic_valid_json(tmp_path, monkeypatch):
    # Isolation: redirect the ENTIRE state tree into tmp_path and assert
    # the producer wrote THERE (never under the real ~/Gaia/state).
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    log = tmp_path / "habits" / "example.log"
    log.parent.mkdir(parents=True)
    base = date(2026, 5, 16)
    log.write_text(
        "\n".join(
            (base - timedelta(days=i)).isoformat() for i in range(5)
        ),
        encoding="utf-8",
    )
    out = producer.produce("example", window_days=60)
    assert out.exists()
    assert out.name == "habit-tracker-habit-grid.json"
    # Wrote under the isolation root, NOT the real state dir.
    assert tmp_path in out.parents
    assert out == tmp_path / "widgets" / "habit-tracker-habit-grid.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["type"] == "contribution-grid"
    assert len(doc["data"]["cells"]) == 60
    # no leftover tmp file
    assert not list(out.parent.glob("*.tmp"))


def test_produce_missing_log_emits_valid_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    out = producer.produce("ghost", window_days=14)
    assert tmp_path in out.parents
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert all(c["level"] == 0 for c in doc["data"]["cells"])
    assert doc["coach"]["tone"] == "nudge"


def test_produce_via_main_cli_is_isolated(tmp_path, monkeypatch):
    # The argparse/CLI entrypoint must honour IGA_STATE_DIR too — the app
    # deletion-invariant test and /gm invoke the producer via its CLI.
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    log = tmp_path / "habits" / "reading.log"
    log.parent.mkdir(parents=True)
    log.write_text(date(2026, 5, 16).isoformat() + "\n", encoding="utf-8")
    rc = producer.main(["--name", "reading", "--days", "30"])
    assert rc == 0
    out = tmp_path / "widgets" / "habit-tracker-habit-grid.json"
    assert out.exists()
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["type"] == "contribution-grid"
    assert len(doc["data"]["cells"]) == 30


def test_state_root_precedence(tmp_path, monkeypatch):
    # IGA_STATE_DIR wins over IGA_HOME; IGA_HOME/state is the fallback;
    # default (neither set) is the real ~/Gaia/state — unchanged behaviour.
    monkeypatch.setenv("IGA_HOME", str(tmp_path / "iga"))
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path / "explicit"))
    assert producer.state_root() == tmp_path / "explicit"
    monkeypatch.delenv("IGA_STATE_DIR")
    assert producer.state_root() == tmp_path / "iga" / "state"
    monkeypatch.delenv("IGA_HOME")
    assert producer.state_root() == Path.home() / "Iga" / "state"


# --------------------------------------------------------------------------- #
# DATA-LOSS GUARD — the producer must NEVER touch the user's real state
# --------------------------------------------------------------------------- #
def _real_state_widget_json() -> Path:
    """Resolve the user's REAL live widget JSON with NO env overrides.

    Deliberately ignores IGA_STATE_DIR / IGA_HOME so we always point at
    the genuine ~/Gaia/state path the live widget reads.
    """
    return (
        Path.home()
        / "Iga"
        / "state"
        / "widgets"
        / "habit-tracker-habit-grid.json"
    )


def test_isolation_guard_real_state_untouched_by_producer(
    tmp_path, monkeypatch
):
    """Running the producer with IGA_STATE_DIR set must NOT create, modify,
    or even stat-touch anything under the real ~/Gaia/state tree.

    Snapshots the real widget JSON's existence + mtime + bytes before, runs
    the producer (CLI + API) fully isolated, and asserts the real file is
    byte-identical and its mtime is unchanged afterwards. If the real file
    does not exist on this machine, assert the producer did NOT create it.
    """
    real = _real_state_widget_json()
    existed_before = real.exists()
    mtime_before = real.stat().st_mtime if existed_before else None
    bytes_before = real.read_bytes() if existed_before else None

    # Fully isolated producer runs (both API and CLI surfaces).
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    log = tmp_path / "habits" / "example.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            (date(2026, 5, 16) - timedelta(days=i)).isoformat()
            for i in range(10)
        ),
        encoding="utf-8",
    )
    producer.produce("example", window_days=120)
    producer.main(["--name", "example", "--days", "120"])

    # The producer wrote into the isolation root only.
    assert (tmp_path / "widgets" / "habit-tracker-habit-grid.json").exists()

    if existed_before:
        assert real.exists(), "producer must not delete the real state file"
        assert real.stat().st_mtime == mtime_before, (
            "REAL ~/Gaia/state widget JSON mtime changed — the producer "
            "wrote to live data despite IGA_STATE_DIR isolation"
        )
        assert real.read_bytes() == bytes_before, (
            "REAL ~/Gaia/state widget JSON bytes changed — data loss"
        )
    else:
        assert not real.exists(), (
            "producer created a file under the real ~/Gaia/state tree "
            "despite IGA_STATE_DIR isolation"
        )


def test_isolation_guard_env_actually_redirects(tmp_path, monkeypatch):
    """Belt-and-braces: with IGA_STATE_DIR set, EVERY resolved producer
    path is under the isolation root and none under the real state dir."""
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    real_root = Path.home() / "Iga" / "state"
    for p in (
        producer.state_root(),
        producer.habits_log_path("example"),
        producer.widget_data_path(),
    ):
        assert tmp_path in (p, *p.parents), f"{p} escaped isolation root"
        assert real_root not in p.parents, f"{p} resolves under real state"
    # Sanity: the real-state resolver helper ignores the override.
    assert real_root in _real_state_widget_json().parents

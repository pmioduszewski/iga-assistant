"""Surfacer tests — ledger-backed surface payload + state refresh, no MCP."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from ledger import Ledger  # noqa: E402
from surfacer import build_surface, refresh_state, DEFAULT_SURFACE_CAP  # noqa: E402


def _ledger_with_done(tmp_path, entries):
    """entries: list of (key, job_id, output_ref). Each becomes a 'done' row."""
    db = tmp_path / "proactive.db"
    led = Ledger(db)
    for key, job_id, ref in entries:
        assert led.claim(key, job_id, 3600) is True
        led.mark(key, "done", output_ref=ref)
    return db


# ---------- build_surface ----------------------------------------------
def test_build_surface_resolves_tldr(tmp_path):
    db = _ledger_with_done(
        tmp_path,
        [
            ("acme::101::2026-05-18", "research-todoist", "drawer:abc"),
        ],
    )
    resolver = lambda ref: {"tldr": "Acme undercuts mid-tier 12%"}
    out = build_surface(db_path=db, output_resolver=resolver)
    assert out["total"] == 1
    assert out["shown"] == 1
    assert out["lines"][0] == "📑 acme: Acme undercuts mid-tier 12%"
    assert out["overflow"] is None


def test_build_surface_project_from_research_key(tmp_path):
    db = _ledger_with_done(
        tmp_path, [("research::555::2026-05-18", "j", "ref1")]
    )
    out = build_surface(
        db_path=db, output_resolver=lambda r: {"tldr": "Found X"}
    )
    assert out["lines"][0] == "📑 research: Found X"


def test_build_surface_unresolved_ref_degrades_not_crashes(tmp_path):
    db = _ledger_with_done(tmp_path, [("p::1", "j", "missing-ref")])
    out = build_surface(db_path=db, output_resolver=lambda r: None)
    assert out["total"] == 1
    assert "(no summary" in out["lines"][0]


def test_build_surface_resolver_exception_degrades(tmp_path):
    db = _ledger_with_done(tmp_path, [("p::1", "j", "ref")])

    def boom(_ref):
        raise RuntimeError("resolver blew up")

    out = build_surface(db_path=db, output_resolver=boom)
    assert out["total"] == 1
    assert "(no summary" in out["lines"][0]


def test_build_surface_caps_with_overflow(tmp_path):
    entries = [(f"proj{i}::k", "j", f"ref{i}") for i in range(8)]
    db = _ledger_with_done(tmp_path, entries)
    out = build_surface(
        db_path=db,
        output_resolver=lambda r: {"tldr": "summary"},
        cap=DEFAULT_SURFACE_CAP,
    )
    assert out["total"] == 8
    assert out["shown"] == DEFAULT_SURFACE_CAP
    assert out["overflow"] == f"+{8 - DEFAULT_SURFACE_CAP} more"


def test_build_surface_ignores_non_done_rows(tmp_path):
    db = tmp_path / "proactive.db"
    led = Ledger(db)
    led.claim("claimed::1", "j", 3600)  # status 'claimed', no output_ref
    led.claim("done::1", "j", 3600)
    led.mark("done::1", "done", output_ref="ref")
    out = build_surface(
        db_path=db, output_resolver=lambda r: {"tldr": "t"}
    )
    assert out["total"] == 1  # only the 'done' row with an output_ref


def test_build_surface_no_db_empty(tmp_path):
    out = build_surface(
        db_path=tmp_path / "nope.db", output_resolver=lambda r: {"tldr": "x"}
    )
    assert out == {"lines": [], "shown": 0, "total": 0, "overflow": None}


# ---------- refresh_state ----------------------------------------------
def test_refresh_state_writes_counts(tmp_path):
    db = tmp_path / "proactive.db"
    led = Ledger(db)
    led.claim("c::1", "j", 3600)  # 'claimed' -> queued
    led.claim("d::1", "j", 3600)
    led.mark("d::1", "done", output_ref="ref")
    led.claim("r::1", "j", 3600)
    led.mark("r::1", "running")

    state_path = tmp_path / "state.json"
    surface = {"lines": ["📑 x: y"], "shown": 1, "total": 1, "overflow": None}
    out_path = refresh_state(
        surface, db_path=db, state_path=state_path,
        governor_stats={"invocations_5h": 0},
    )
    assert out_path == state_path
    doc = json.loads(state_path.read_text())
    assert doc["schema_version"] == 1
    assert doc["surface"] == surface
    assert doc["counts"]["queued"] == 1
    assert doc["counts"]["running"] == 1
    assert doc["counts"]["done"] == 1
    assert doc["governor"] == {"invocations_5h": 0}
    assert "generated_at" in doc


def test_refresh_state_atomic_no_tmp(tmp_path):
    db = tmp_path / "proactive.db"
    Ledger(db)
    sp = tmp_path / "s.json"
    refresh_state({"lines": []}, db_path=db, state_path=sp)
    assert sp.is_file()
    assert not (tmp_path / "s.json.tmp").exists()

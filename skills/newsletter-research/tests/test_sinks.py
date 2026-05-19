"""Tests for the generic finding-sink contract (sinks.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

import sinks as sk  # type: ignore
from sinks import SqliteSink, normalize_sinks  # type: ignore


# --------------------------------------------------------------------------- #
# normalize_sinks
# --------------------------------------------------------------------------- #
def test_no_config_defaults_to_sqlite_floor():
    assert normalize_sinks({}) == [{"type": "sqlite"}]


def test_legacy_todoist_project_yields_sqlite_plus_todoist():
    out = normalize_sinks({"todoist_project": "Iga Research"})
    assert out == [
        {"type": "sqlite"},
        {"type": "todoist", "project": "Iga Research"},
    ]


def test_explicit_sinks_list_of_typenames_attaches_todoist_project():
    out = normalize_sinks(
        {"sinks": ["sqlite", "todoist"], "todoist_project": "X"}
    )
    assert {"type": "sqlite"} in out
    assert {"type": "todoist", "project": "X"} in out
    assert out[0]["type"] == "sqlite"  # local floor first


def test_sqlite_floor_always_added_even_if_only_todoist_listed():
    out = normalize_sinks({"sinks": ["todoist"], "todoist_project": "P"})
    assert any(s["type"] == "sqlite" for s in out)


def test_todoist_without_project_is_dropped_not_errored():
    out = normalize_sinks({"sinks": ["todoist"]})  # no todoist_project
    assert out == [{"type": "sqlite"}]  # floor holds, no broken todoist


def test_mempalace_is_implicit_and_stripped_if_listed():
    out = normalize_sinks({"sinks": ["mempalace", "sqlite"]})
    assert all(s["type"] != "mempalace" for s in out)
    assert out == [{"type": "sqlite"}]


def test_unknown_sink_type_raises():
    with pytest.raises(ValueError):
        normalize_sinks({"sinks": ["smoke-signal"]})


# --------------------------------------------------------------------------- #
# state-root isolation
# --------------------------------------------------------------------------- #
def test_findings_db_path_honours_iga_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("IGA_FINDINGS_DB", raising=False)
    assert sk.findings_db_path() == tmp_path / "findings.db"


def test_findings_db_explicit_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("IGA_FINDINGS_DB", str(tmp_path / "x.db"))
    assert sk.findings_db_path() == tmp_path / "x.db"


# --------------------------------------------------------------------------- #
# SqliteSink — idempotent append
# --------------------------------------------------------------------------- #
def _f(key, **kw):
    base = {
        "finding_key": key, "title": "T", "type": "lib", "url": "u",
        "project": "general", "fit": 3, "why": "w", "source": "s",
        "hook": "dev-libs", "ts": "2026-05-19",
    }
    base.update(kw)
    return base


def test_append_inserts_and_dedups(tmp_path):
    s = SqliteSink(tmp_path / "f.db")
    a1, d1 = s.append([_f("k1"), _f("k2")])
    assert (a1, d1) == (2, 0)
    # re-append same keys → all dupes, no error, count stable
    a2, d2 = s.append([_f("k1"), _f("k2")])
    assert (a2, d2) == (0, 2)
    # mix new + dup
    a3, d3 = s.append([_f("k2"), _f("k3")])
    assert (a3, d3) == (1, 1)


def test_append_empty_is_noop(tmp_path):
    s = SqliteSink(tmp_path / "f.db")
    assert s.append([]) == (0, 0)


def test_append_skips_rows_without_key(tmp_path):
    s = SqliteSink(tmp_path / "f.db")
    a, d = s.append([_f(""), _f("real")])
    assert a == 1


def test_non_integer_fit_stored_as_null_not_crash(tmp_path):
    s = SqliteSink(tmp_path / "f.db")
    a, _ = s.append([_f("k", fit="n/a")])
    assert a == 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_append_reads_json_and_reports(tmp_path, capsys):
    db = tmp_path / "cli.db"
    payload = json.dumps([_f("c1"), _f("c2")])
    pj = tmp_path / "in.json"
    pj.write_text(payload, encoding="utf-8")
    rc = sk.main(["append", "--db", str(db), "--json", str(pj)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "appended=2 skipped_dupes=0" in out

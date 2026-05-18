"""The Iga-facing read path (digest). Read-only, deterministic, no clock
except explicit today; reuses the frozen widget builder; isolation-safe."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from _engine import import_habitkit as imp
from _engine import substrate as sub
from _engine import summary as smry
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)
_ENGINE = Path(__file__).resolve().parents[1] / "engine"


def _seed(tmp_path):
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")
    imp.import_file(src, tmp_path)


def test_build_summary_shape_and_determinism(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    d1 = smry.build_summary(today=TODAY)
    d2 = smry.build_summary(today=TODAY)
    assert d1 == d2, "same substrate + today → byte-identical digest"
    assert set(d1) >= {
        "date", "active_count", "done_today", "focus",
        "archived_count", "habits", "nudges", "milestones",
    }
    assert d1["date"] == "2026-05-16"
    assert d1["active_count"] == len(d1["habits"])
    # nudges are exactly the accountability kinds, milestones separate.
    for r in d1["nudges"]:
        assert r["coach_kind"] in {"at-risk", "slipped", "dormant"}
    for r in d1["milestones"]:
        assert r["coach_kind"] == "milestone"
    # render is non-empty Markdown, one header line.
    md = smry.render_markdown(d1)
    assert md.startswith("**Habits — 2026-05-16**")
    assert isinstance(md, str) and len(md) > 0


def test_summary_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    subp = tmp_path / "substrates" / "habit-tracker.json"
    before = subp.read_bytes()
    smry.build_summary(today=TODAY)
    assert subp.read_bytes() == before, "digest must not mutate state"


def test_summary_cli_markdown_and_json(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    md = subprocess.run(
        [sys.executable, str(_ENGINE / "summary.py"),
         "--today", "2026-05-16"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "IGA_STATE_DIR": str(tmp_path)})
    assert md.returncode == 0 and "Habits — 2026-05-16" in md.stdout
    js = subprocess.run(
        [sys.executable, str(_ENGINE / "summary.py"),
         "--today", "2026-05-16", "--json"],
        capture_output=True, text=True,
        env={**__import__("os").environ, "IGA_STATE_DIR": str(tmp_path)})
    assert js.returncode == 0
    parsed = json.loads(js.stdout)
    assert parsed["date"] == "2026-05-16" and "habits" in parsed


def test_summary_empty_substrate_graceful(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    d = smry.build_summary(today=TODAY)
    assert d["active_count"] == 0 and d["nudges"] == []
    assert "Nothing at risk" in smry.render_markdown(d)

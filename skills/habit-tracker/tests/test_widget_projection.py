"""Derived widget projection: the v2 contribution-grid contract is preserved.

The running app polls habit-tracker-habit-grid.json (schema_version 1). These
tests prove the substrate-derived projection emits the SAME v1 shape and that
projecting NEVER clobbers the user's real live widget JSON.
"""

from __future__ import annotations

import json
from datetime import date

from _engine import import_habitkit as imp
from _engine import substrate as sub
from _engine import widget_projection as wp
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)


def test_projection_emits_v1_schema_shape():
    s = imp.import_habitkit(habitkit_export())
    payload = wp.build_widget_from_substrate(
        s, today=TODAY, window_days=120
    )
    assert payload["schema_version"] == 1
    assert payload["widget_id"] == "habit-grid"
    assert payload["type"] == "contribution-grid"
    d = payload["data"]
    assert set(d.keys()) == {"label", "levels", "cells"}
    assert len(d["cells"]) == 120
    for c in d["cells"]:
        assert set(c.keys()) == {"date", "level"}
        assert 0 <= c["level"] <= d["levels"]
    assert set(payload["coach"].keys()) == {"text", "tone"}
    json.dumps(payload)  # serialisable


def test_projection_lights_done_days():
    s = imp.import_habitkit(habitkit_export())
    payload = wp.build_widget_from_substrate(
        s, entity_id="h-reading", today=TODAY, window_days=30
    )
    by_date = {c["date"]: c["level"] for c in payload["data"]["cells"]}
    # Reading done 05-14,05-15,05-16 -> those cells nonzero
    assert by_date["2026-05-16"] >= 1
    assert by_date["2026-05-15"] >= 1
    assert by_date["2026-05-14"] >= 1
    assert by_date["2026-05-01"] == 0
    nonzero = sum(1 for c in payload["data"]["cells"] if c["level"] > 0)
    assert nonzero >= 3


def test_projection_picks_first_non_archived_by_default():
    s = imp.import_habitkit(habitkit_export())
    payload = wp.build_widget_from_substrate(s, today=TODAY)
    # default = h-reading (order_index 0, not archived)
    assert "Reading" in payload["data"]["label"]


def test_projection_empty_substrate_is_graceful():
    s = sub.Substrate(substrate_kind="habit-tracker")
    payload = wp.build_widget_from_substrate(
        s, today=TODAY, window_days=14
    )
    assert payload["schema_version"] == 1
    assert all(c["level"] == 0 for c in payload["data"]["cells"])
    assert payload["coach"]["tone"] == "nudge"


def test_project_writes_isolated_path_only(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = imp.import_habitkit(habitkit_export())
    sub.SubstrateStore("habit-tracker").save(s)
    out = wp.project(window_days=60)
    assert tmp_path in out.parents
    assert out.name == "habit-tracker-habit-grid.json"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["type"] == "contribution-grid"
    assert len(doc["data"]["cells"]) == 60
    assert not list(out.parent.glob("*.tmp"))


def test_project_cli_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = imp.import_habitkit(habitkit_export())
    sub.SubstrateStore("habit-tracker").save(s)
    rc = wp.main(["--days", "30"])
    assert rc == 0
    out = tmp_path / "widgets" / "habit-tracker-habit-grid.json"
    assert out.exists()
    assert len(json.loads(out.read_text())["data"]["cells"]) == 30

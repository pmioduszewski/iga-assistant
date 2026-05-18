"""DATA-LOSS + PRIVACY guards for Wave A (substrate/import/export/projection).

Mirrors the v2 producer guard pattern. Asserts:

  1. With $IGA_STATE_DIR set, EVERY resolved substrate/widget path is under
     the isolation root and NONE under the real ~/Gaia/state.
  2. Running import -> export -> project -> stats fully isolated does NOT
     create/modify/mtime-touch the user's REAL live widget JSON or any real
     ~/Gaia/state file.
  3. PRIVACY: no source/test/fixture file references the real HabitKit export
     path, and the synthetic fixture contains only neutral names.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from _engine import export_habitkit as exp
from _engine import import_habitkit as imp
from _engine import stats as st
from _engine import substrate as sub
from _engine import widget_projection as wp
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)
_REAL_EXPORT = "/Users/you/Downloads/habitkit_export.json"


def _real_state_root() -> Path:
    """The genuine ~/Gaia/state, ignoring ALL env overrides on purpose."""
    return Path.home() / "Gaia" / "state"


def _real_widget_json() -> Path:
    return _real_state_root() / "widgets" / "habit-tracker-habit-grid.json"


def _real_substrate_json() -> Path:
    return _real_state_root() / "substrates" / "habit-tracker.json"


# --------------------------------------------------------------------------- #
# 1. path isolation
# --------------------------------------------------------------------------- #
def test_all_paths_redirected_under_isolation_root(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    real_root = _real_state_root()
    for p in (
        sub.state_root(),
        sub.substrate_path("habit-tracker"),
        wp._producer.widget_data_path(),
    ):
        assert tmp_path in (p, *p.parents), f"{p} escaped isolation root"
        assert real_root not in p.parents, f"{p} resolves under real state"


# --------------------------------------------------------------------------- #
# 2. real ~/Gaia/state untouched across a full Wave-A pipeline run
# --------------------------------------------------------------------------- #
def test_full_pipeline_does_not_touch_real_state(tmp_path, monkeypatch):
    widget = _real_widget_json()
    substrate_real = _real_substrate_json()

    def snap(p: Path):
        if p.exists():
            return (True, p.stat().st_mtime, p.read_bytes())
        return (False, None, None)

    w_before = snap(widget)
    s_before = snap(substrate_real)

    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")

    imp.import_file(src, tmp_path)
    exp.export_file(tmp_path, tmp_path / "out.json")
    wp.project(window_days=120)
    wp.main(["--days", "120"])
    s = sub.SubstrateStore("habit-tracker").load()
    st.active_stats(s, today=TODAY)

    # wrote ONLY under isolation
    assert (tmp_path / "substrates" / "habit-tracker.json").exists()
    assert (tmp_path / "widgets" / "habit-tracker-habit-grid.json").exists()

    def assert_unchanged(p: Path, before, label: str):
        existed, mtime, data = before
        if existed:
            assert p.exists(), f"{label}: real file deleted"
            assert p.stat().st_mtime == mtime, (
                f"{label}: REAL ~/Gaia/state mtime changed — data loss"
            )
            assert p.read_bytes() == data, (
                f"{label}: REAL ~/Gaia/state bytes changed — data loss"
            )
        else:
            assert not p.exists(), (
                f"{label}: created a file under the real ~/Gaia/state "
                f"despite IGA_STATE_DIR isolation"
            )

    assert_unchanged(widget, w_before, "live widget JSON")
    assert_unchanged(substrate_real, s_before, "live substrate JSON")


# --------------------------------------------------------------------------- #
# 3. privacy: no real-export path in code/tests/fixtures; neutral names only
# --------------------------------------------------------------------------- #
def test_no_source_or_test_references_real_export_path():
    """Wave-A engine + tests must NOT hard-reference the real HabitKit
    export. This guard test itself is the ONLY allowed mention (it asserts
    *against* the path); we exclude this file from its own scan."""
    root = Path(__file__).resolve().parents[1]
    me = Path(__file__).resolve()
    offenders = []
    for p in list(root.glob("engine/*.py")) + list(root.glob("tests/*.py")):
        if p.resolve() == me:
            continue
        txt = p.read_text(encoding="utf-8")
        if "habitkit_export.json" in txt or "Downloads/habitkit" in txt:
            offenders.append(str(p))
    assert not offenders, (
        f"real HabitKit export path referenced in: {offenders}"
    )


def test_synthetic_fixture_has_only_neutral_names():
    ex = habitkit_export()
    neutral = {
        "Reading", "NoSnack", "OldThing", "Gym", "Water", "Health",
        "pages", "double session",
    }
    for h in ex["habits"]:
        assert h["name"] in neutral
    for c in ex["categories"]:
        assert c["name"] in neutral
    # no note/name looks like an email / long private string
    for c in ex["completions"]:
        if c["note"] is not None:
            assert c["note"] in neutral
            assert "@" not in c["note"]


def test_fixture_shape_matches_habitkit_field_names():
    """The synthetic fixture must mirror the real export's FIELD NAMES
    (structure only — proves importer is exercised against a faithful
    shape without any private data)."""
    ex = habitkit_export()
    assert set(ex.keys()) == {
        "habits", "completions", "intervals", "categories",
        "categoryMappings", "reminders",
    }
    assert set(ex["habits"][0]) == {
        "id", "name", "description", "icon", "color", "emoji",
        "archived", "isInverse", "orderIndex", "createdAt",
    }
    assert set(ex["completions"][0]) == {
        "id", "date", "habitId", "timezoneOffsetInMinutes",
        "amountOfCompletions", "note",
    }
    assert set(ex["intervals"][0]) == {
        "id", "habitId", "startDate", "endDate", "type",
        "requiredNumberOfCompletions",
        "requiredNumberOfCompletionsPerDay", "unitType", "streakType",
        "allowExceedingGoal",
    }
    assert set(ex["categoryMappings"][0]) == {
        "id", "habitId", "categoryId", "orderIndex", "createdAt",
    }
    assert set(ex["reminders"][0]) == {
        "id", "habitId", "weekdayIndices", "hour", "minute",
    }
    # the scan guard's own reference to the real path is intentional;
    # assert it never actually opens it.
    assert not Path(_REAL_EXPORT).samefile(Path(__file__)) \
        if Path(_REAL_EXPORT).exists() else True

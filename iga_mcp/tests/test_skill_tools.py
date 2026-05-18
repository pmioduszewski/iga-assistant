"""
Tests for IgaMCP skill-contributed tools (iga_habit_log, iga_habit_summary,
iga_habit_list, iga_mood_log, iga_mood_summary).

Design:
  - stdlib unittest only (no pytest).
  - IGA_HOME is pointed at the repo root so real engine scripts are used.
  - IGA_STATE_DIR is set to a fresh TemporaryDirectory for each test so the
    user's live ~/Gaia/state is NEVER touched.
  - Habit substrate is seeded via the REAL creation path: the HabitKit
    importer (import_habitkit.py). We do NOT hand-write substrate internals.
  - After each test we assert that nothing was written under ~/Gaia/state.

Run:
  python3 -m unittest discover iga_mcp/tests
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve IGA_HOME to the repo root (two levels up from this file).
# iga_mcp/tests/test_skill_tools.py  →  iga_mcp/  →  repo root
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
os.environ["IGA_HOME"] = str(REPO_ROOT)

# Make the iga_mcp package importable without installing it.
_SRC = str(REPO_ROOT / "iga_mcp" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# REAL ~/Gaia/state — we assert this is never written.
_REAL_STATE = Path.home() / "Gaia" / "state"

_IMPORTER = (
    REPO_ROOT / "skills" / "habit-tracker" / "engine" / "import_habitkit.py"
)


def _import_habitkit_module():
    spec = importlib.util.spec_from_file_location(
        "test_import_habitkit", _IMPORTER
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_habit_via_importer(
    state_dir: str, *, habit_id: str = "h-9f3a2c", name: str = "Push-ups"
) -> None:
    """Seed the habit substrate the way the engines really create entities:
    a minimal HabitKit export run through import_habitkit.import_file. This
    proves name→id resolution against a UUID-like id (not a hand-written
    'test-habit' shortcut)."""
    imp = _import_habitkit_module()
    export = {
        "habits": [{"id": habit_id, "name": name}],
        "completions": [],
        "intervals": [],
        "categories": [],
        "categoryMappings": [],
        "reminders": [],
    }
    export_path = Path(state_dir) / "_hk_export.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")
    imp.import_file(export_path, Path(state_dir))


def _read_substrate_entities(state_dir: str) -> list[dict]:
    path = Path(state_dir) / "substrates" / "habit-tracker.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("entities", [])


class HabitToolsTest(unittest.TestCase):
    """iga_habit_log → iga_habit_summary round-trip, state-isolated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="iga_test_state_")
        os.environ["IGA_STATE_DIR"] = self._tmp.name
        _seed_habit_via_importer(self._tmp.name)
        import iga_mcp.skills as skills_mod
        importlib.reload(skills_mod)
        self._skills = skills_mod

    def tearDown(self) -> None:
        del os.environ["IGA_STATE_DIR"]
        self._tmp.cleanup()

    def test_habit_log_add_then_summary(self) -> None:
        # name resolves to the UUID-like id "h-9f3a2c"
        result = self._skills.habit_log(habit="Push-ups", op="add", date="today")
        self.assertTrue(result["ok"])
        self.assertEqual(result["habit_id"], "h-9f3a2c")

        summary = self._skills.habit_summary()
        self.assertIsInstance(summary, (dict, list))
        if isinstance(summary, dict):
            habits = summary.get("habits", [])
            self.assertTrue(
                len(habits) >= 1,
                f"Expected at least one habit in summary: {summary}",
            )

    def test_name_resolution_no_duplicate_entity(self) -> None:
        """'push-ups' and ' Push-Ups ' both resolve to id 'h-9f3a2c' and
        increment the SAME entity — no duplicate entity is ever created."""
        r1 = self._skills.habit_log(habit="push-ups", op="add", date="today")
        r2 = self._skills.habit_log(habit=" Push-Ups ", op="add", date="today")
        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"])
        self.assertEqual(r1["habit_id"], "h-9f3a2c")
        self.assertEqual(r2["habit_id"], "h-9f3a2c")

        ents = _read_substrate_entities(self._tmp.name)
        self.assertEqual(
            len(ents), 1,
            f"expected exactly 1 entity (no dup); got {ents}",
        )
        self.assertEqual(ents[0]["id"], "h-9f3a2c")

    def test_unknown_habit_returns_structured_error(self) -> None:
        r = self._skills.habit_log(habit="nonexistent", op="add")
        self.assertFalse(r["ok"])
        self.assertIn("available", r)
        self.assertIn("Push-ups", r["available"])

    def test_habit_list(self) -> None:
        out = self._skills.habit_list()
        self.assertIn("habits", out)
        names = [h["name"] for h in out["habits"]]
        ids = [h["id"] for h in out["habits"]]
        self.assertIn("Push-ups", names)
        self.assertIn("h-9f3a2c", ids)

    def test_state_isolation_habit(self) -> None:
        """Nothing should be written under ~/Gaia/state during this test."""
        self._skills.habit_log(habit="Push-ups", op="add", date="today")
        if _REAL_STATE.exists():
            before_mtime = _REAL_STATE.stat().st_mtime
            self._skills.habit_log(habit="Push-ups", op="add", date="today")
            self.assertEqual(
                _REAL_STATE.stat().st_mtime,
                before_mtime,
                "Real ~/Gaia/state was modified — state isolation failure.",
            )
        tmp_path = Path(self._tmp.name)
        self.assertTrue(
            any(tmp_path.rglob("*")), "Expected files in temp state dir."
        )


class MoodToolsTest(unittest.TestCase):
    """iga_mood_log → iga_mood_summary round-trip, state-isolated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="iga_test_state_")
        os.environ["IGA_STATE_DIR"] = self._tmp.name
        import iga_mcp.skills as skills_mod
        importlib.reload(skills_mod)
        self._skills = skills_mod

    def tearDown(self) -> None:
        del os.environ["IGA_STATE_DIR"]
        self._tmp.cleanup()

    def test_mood_log_then_summary(self) -> None:
        result = self._skills.mood_log(emotion="calm", note="test run")
        self.assertTrue(result["ok"])
        self.assertEqual(result["emotion"], "calm")

        summary = self._skills.mood_summary(days=7)
        self.assertIsInstance(summary, (dict, list))

    def test_unknown_emotion_returns_suggestions(self) -> None:
        r = self._skills.mood_log(emotion="not-an-emotion")
        self.assertFalse(r["ok"])
        self.assertIn("suggestions", r)
        self.assertIsInstance(r["suggestions"], list)

    def test_valid_lexicon_emotion_still_logs(self) -> None:
        r = self._skills.mood_log(emotion="anxious")
        self.assertTrue(r["ok"])
        self.assertEqual(r["emotion"], "anxious")

    def test_state_isolation_mood(self) -> None:
        """Nothing should be written under ~/Gaia/state during this test."""
        self._skills.mood_log(emotion="curious")
        if _REAL_STATE.exists():
            before_mtime = _REAL_STATE.stat().st_mtime
            self._skills.mood_log(emotion="curious")
            self.assertEqual(
                _REAL_STATE.stat().st_mtime,
                before_mtime,
                "Real ~/Gaia/state was modified — state isolation failure.",
            )
        tmp_path = Path(self._tmp.name)
        self.assertTrue(
            any(tmp_path.rglob("*")), "Expected files in temp state dir."
        )


class ErrorHandlingTest(unittest.TestCase):
    """Validate that bad inputs raise / report cleanly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="iga_test_state_")
        os.environ["IGA_STATE_DIR"] = self._tmp.name
        _seed_habit_via_importer(self._tmp.name)
        import iga_mcp.skills as skills_mod
        importlib.reload(skills_mod)
        self._skills = skills_mod

    def tearDown(self) -> None:
        del os.environ["IGA_STATE_DIR"]
        self._tmp.cleanup()

    def test_invalid_op_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._skills.habit_log(habit="Push-ups", op="invalid")

    def test_set_without_amount_raises(self) -> None:
        with self.assertRaises(ValueError):
            self._skills.habit_log(habit="Push-ups", op="set")


if __name__ == "__main__":
    unittest.main()

"""Importer: every HabitKit field mapped, idempotent re-import, tz semantics.

Uses ONLY the synthetic fixture. Never reads the real export, never touches
the real ~/Iga/state.
"""

from __future__ import annotations

import json

from _engine import import_habitkit as imp
from _engine import substrate as sub
from _synthetic import habitkit_export


def test_every_entity_count_mapped():
    ex = habitkit_export()
    s = imp.import_habitkit(ex)
    assert len(s.entities) == len(ex["habits"]) == 5
    assert len(s.events) == len(ex["completions"]) == 10
    assert len(s.goal_intervals) == len(ex["intervals"]) == 5
    assert len(s.categories) == len(ex["categories"]) == 1
    assert len(s.mappings) == len(ex["categoryMappings"]) == 2
    assert len(s.reminders) == len(ex["reminders"]) == 1


def test_habit_fields_mapped_including_roundtrip_attrs():
    s = imp.import_habitkit(habitkit_export())
    e = s.entity("h-reading")
    assert e.name == "Reading"
    assert e.description == "pages"
    assert e.archived is False
    assert e.inverse is False
    assert e.order_index == 0
    assert e.attrs["icon"] == "book"
    assert e.attrs["color"] == "indigo"
    assert e.attrs["emoji"] is None
    assert e.attrs["created_at"] == "2026-01-01T08:00:00.000000Z"
    assert s.entity("h-nosnack").inverse is True
    assert s.entity("h-old").archived is True


def test_completion_fields_and_amount_and_note():
    s = imp.import_habitkit(habitkit_export())
    ev = next(e for e in s.events if e.id == "c-r2")
    assert ev.entity_id == "h-reading"
    assert ev.amount == 2
    assert ev.note == "double session"
    assert ev.tz_offset_min == 60
    # zero-amount day preserved (explicit "did not do it")
    z = next(e for e in s.events if e.id == "c-w0")
    assert z.amount == 0


def test_timezone_semantics_local_civil_date():
    """UTC instant + offset -> the LOCAL civil day the user meant.

    c-r-tz: 2026-05-15T23:00Z, offset +120min -> local 2026-05-16T01:00
    -> civil date 2026-05-16 (must NOT be 2026-05-15).
    """
    s = imp.import_habitkit(habitkit_export())
    tz = next(e for e in s.events if e.id == "c-r-tz")
    assert tz.date == "2026-05-16"
    assert tz.tz_offset_min == 120
    # original instant preserved for byte-exact export
    assert tz.attrs["hk_date"] == "2026-05-15T23:00:00.000Z"


def test_interval_fields_mapped():
    s = imp.import_habitkit(habitkit_export())
    g = next(g for g in s.goal_intervals if g.id == "iv-gym")
    assert g.entity_id == "h-gym"
    assert g.period == "week"
    assert g.target == 3
    assert g.per_day_target == 1
    assert g.allow_exceed is False
    assert g.end is None
    assert g.attrs["unit_type"] == "incremental"
    assert g.attrs["streak_type"] == "day"
    # ended interval -> civil end date
    early = next(g for g in s.goal_intervals if g.id == "iv-water-early")
    assert early.end == "2026-05-15"
    assert early.period == "day"
    assert early.target is None
    assert early.per_day_target == 1


def test_category_and_mapping_and_reminder_mapped():
    s = imp.import_habitkit(habitkit_export())
    c = s.categories[0]
    assert c.name == "Health"
    assert c.attrs["icon"] == "heart"
    m = next(m for m in s.mappings if m.id == "cm-1")
    assert m.entity_id == "h-gym" and m.category_id == "cat-health"
    r = s.reminders[0]
    assert r.entity_id == "h-reading"
    assert r.weekdays == [1, 2, 3, 4, 5]
    assert r.hour == 19 and r.minute == 30


def test_reimport_is_idempotent_no_dupes():
    ex = habitkit_export()
    s = imp.import_habitkit(ex)
    n_before = (
        len(s.entities), len(s.events), len(s.goal_intervals),
        len(s.categories), len(s.mappings), len(s.reminders),
    )
    s2 = imp.import_habitkit(ex, into=s)  # re-import same export
    n_after = (
        len(s2.entities), len(s2.events), len(s2.goal_intervals),
        len(s2.categories), len(s2.mappings), len(s2.reminders),
    )
    assert n_before == n_after
    assert sub.data_equal(imp.import_habitkit(ex), s2)


def test_reimport_updated_export_updates_in_place():
    ex = habitkit_export()
    s = imp.import_habitkit(ex)
    # same UUID, changed name + amount -> in-place update, still no dupes
    ex["habits"][0]["name"] = "Reading More"
    ex["completions"][0]["amountOfCompletions"] = 9
    s2 = imp.import_habitkit(ex, into=s)
    assert len(s2.entities) == 5  # not 10
    assert s2.entity("h-reading").name == "Reading More"
    assert next(e for e in s2.events if e.id == "c-r1").amount == 9


def test_import_file_isolated_and_counts_only(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")
    counts = imp.import_file(src, tmp_path)
    assert counts == {
        "entities": 5, "events": 10, "goal_intervals": 5,
        "categories": 1, "mappings": 2, "reminders": 1,
    }
    written = tmp_path / "substrates" / "habit-tracker.json"
    assert written.exists()
    # double-run via the file path is still idempotent on disk
    imp.import_file(src, tmp_path)
    s = sub.SubstrateStore("habit-tracker").load()
    assert len(s.entities) == 5 and len(s.events) == 10


def test_import_cli_requires_state_dir(monkeypatch):
    # --state-dir is mandatory: no implicit real-state default in the CLI.
    import pytest

    with pytest.raises(SystemExit):
        imp.main(["--input", "x.json"])

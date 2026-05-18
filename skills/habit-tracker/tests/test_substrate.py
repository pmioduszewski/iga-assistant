"""Generic substrate contract: serialization stability, forward-compat
parsing, atomic + isolation-aware persistence, data_equal semantics."""

from __future__ import annotations

import json
from pathlib import Path

from _engine import substrate as sub


def _mini():
    return sub.Substrate(
        substrate_kind="habit-tracker",
        entities=[sub.Entity(id="e1", name="Reading", order_index=0)],
        events=[
            sub.Event(id="ev1", entity_id="e1", date="2026-05-16", amount=2)
        ],
        goal_intervals=[
            sub.GoalInterval(
                id="g1", entity_id="e1", start="2026-01-01", period="day",
                per_day_target=1,
            )
        ],
        categories=[sub.Category(id="c1", name="Health")],
        mappings=[sub.Mapping(id="m1", entity_id="e1", category_id="c1")],
        reminders=[
            sub.Reminder(id="r1", entity_id="e1", weekdays=[1, 2], hour=9)
        ],
    )


def test_to_from_doc_is_data_stable():
    s = _mini()
    again = sub.from_doc(sub.to_doc(s))
    assert sub.data_equal(s, again)


def test_serialization_is_deterministic_bytes():
    s = _mini()
    a = sub.to_doc(s)
    b = sub.to_doc(s)
    a.pop("generated_at")
    b.pop("generated_at")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generated_at_is_not_part_of_data_equality():
    s1 = _mini()
    s2 = _mini()
    d1, d2 = sub.to_doc(s1), sub.to_doc(s2)
    # generated_at differs by wall clock but data_equal must ignore it
    assert sub.data_equal(s1, s2)
    assert "generated_at" in d1 and "generated_at" in d2


def test_from_doc_tolerates_unknown_future_field():
    doc = sub.to_doc(_mini())
    doc["entities"][0]["some_future_field"] = "ignored"
    parsed = sub.from_doc(doc)  # must not raise
    assert parsed.entities[0].name == "Reading"


def test_store_roundtrip_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    store = sub.SubstrateStore("habit-tracker")
    s = _mini()
    path = store.save(s)
    assert tmp_path in path.parents
    assert path == tmp_path / "substrates" / "habit-tracker.json"
    loaded = store.load()
    assert sub.data_equal(s, loaded)
    assert not list(path.parent.glob("*.tmp"))  # atomic, no leftover tmp


def test_store_load_missing_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = sub.SubstrateStore("habit-tracker").load()
    assert s.entities == [] and s.events == []
    assert s.substrate_kind == "habit-tracker"


def test_store_load_corrupt_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    p = tmp_path / "substrates" / "habit-tracker.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not json", encoding="utf-8")
    s = sub.SubstrateStore("habit-tracker").load()
    assert s.entities == []


def test_substrate_path_honours_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    p = sub.substrate_path("habit-tracker")
    assert tmp_path in p.parents
    real = Path.home() / "Iga" / "state"
    assert real not in p.parents


def test_substrate_kind_is_generic_discriminator(tmp_path, monkeypatch):
    # The store layer is domain-agnostic: a different kind is a different
    # instance of the SAME contract (proves "habit" is not hard-coded).
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    mood = sub.Substrate(substrate_kind="mood")
    mood.entities.append(sub.Entity(id="m1", name="Calm"))
    store = sub.SubstrateStore("mood")
    store.save(mood)
    assert (tmp_path / "substrates" / "mood.json").exists()
    back = store.load()
    assert back.substrate_kind == "mood"
    assert sub.data_equal(mood, back)

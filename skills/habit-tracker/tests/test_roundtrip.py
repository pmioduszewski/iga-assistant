"""Round-trip fixpoint: import(export(substrate)) data-equals substrate.

This is the anti-lock-in guarantee. THE assertion that gates Definition of
Done item 2 is ``test_import_export_import_is_fixpoint``.
"""

from __future__ import annotations

from _engine import export_habitkit as exp
from _engine import import_habitkit as imp
from _engine import substrate as sub
from _synthetic import habitkit_export


def test_import_export_import_is_fixpoint():
    """import(export(import(HK))) == import(HK)  — the fixpoint.

    Importing the synthetic HabitKit export gives substrate S. Exporting S
    back to HabitKit JSON then re-importing it MUST yield a substrate
    data-equal to S, for the supported field set.
    """
    s = imp.import_habitkit(habitkit_export())
    hk_again = exp.export_habitkit(s)
    s2 = imp.import_habitkit(hk_again)
    assert sub.data_equal(s, s2), (
        "round-trip fixpoint broken: import(export(S)) != S"
    )
    # and it is a true fixpoint — a SECOND round trip is also a no-op.
    s3 = imp.import_habitkit(exp.export_habitkit(s2))
    assert sub.data_equal(s2, s3)


def test_export_preserves_byte_exact_original_instants():
    """For an imported substrate the exporter re-emits the ORIGINAL HabitKit
    instants verbatim (hk_date / hk_start passthrough) — byte-exact."""
    ex = habitkit_export()
    s = imp.import_habitkit(ex)
    out = exp.export_habitkit(s)
    by_id = {c["id"]: c for c in out["completions"]}
    src = {c["id"]: c for c in ex["completions"]}
    for cid in src:
        assert by_id[cid]["date"] == src[cid]["date"]
        assert (
            by_id[cid]["timezoneOffsetInMinutes"]
            == src[cid]["timezoneOffsetInMinutes"]
        )
    iv_out = {i["id"]: i for i in out["intervals"]}
    iv_src = {i["id"]: i for i in ex["intervals"]}
    for iid in iv_src:
        assert iv_out[iid]["startDate"] == iv_src[iid]["startDate"]
        assert iv_out[iid]["endDate"] == iv_src[iid]["endDate"]


def test_export_shape_matches_habitkit_top_level():
    s = imp.import_habitkit(habitkit_export())
    out = exp.export_habitkit(s)
    assert set(out.keys()) == {
        "habits", "completions", "intervals", "categories",
        "categoryMappings", "reminders",
    }
    h = out["habits"][0]
    assert set(h.keys()) == {
        "id", "name", "description", "icon", "color", "emoji",
        "archived", "isInverse", "orderIndex", "createdAt",
    }
    c = out["completions"][0]
    assert set(c.keys()) == {
        "id", "date", "habitId", "timezoneOffsetInMinutes",
        "amountOfCompletions", "note",
    }
    iv = out["intervals"][0]
    assert set(iv.keys()) == {
        "id", "habitId", "startDate", "endDate", "type",
        "requiredNumberOfCompletions",
        "requiredNumberOfCompletionsPerDay", "unitType", "streakType",
        "allowExceedingGoal",
    }


def test_native_substrate_roundtrip_is_idempotent_normalizer():
    """A natively-authored substrate (no hk_* provenance attrs) survives the
    round trip with all DOMAIN fields preserved exactly, and ``import∘export``
    is an idempotent normalizer: once the data has passed through one round
    trip it is a fixpoint (``S2 == import(export(S2))``).

    (The first pass legitimately *adds* HabitKit provenance attrs — hk_date,
    unit_type/streak_type — synthesized so the EXPORT is a valid HabitKit
    file. Those are round-trip provenance, not domain data; the meaningful
    guarantee is domain-field preservation + idempotence, which is exactly
    the fixpoint the HabitKit-origin path also satisfies.)
    """
    s = sub.Substrate(substrate_kind="habit-tracker")
    s.entities.append(sub.Entity(id="n1", name="Water"))
    s.events.append(
        sub.Event(
            id="nv1", entity_id="n1", date="2026-05-16", amount=3,
            tz_offset_min=120,
        )
    )
    s.goal_intervals.append(
        sub.GoalInterval(
            id="ng1", entity_id="n1", start="2026-01-01", period="day",
            per_day_target=2,
        )
    )
    s2 = imp.import_habitkit(exp.export_habitkit(s))
    ev = s2.events[0]
    # domain fields preserved exactly through the round trip
    assert ev.date == "2026-05-16"
    assert ev.tz_offset_min == 120
    assert ev.amount == 3
    g = s2.goal_intervals[0]
    assert (g.period, g.per_day_target, g.start) == ("day", 2, "2026-01-01")
    # idempotent normalizer: a second round trip is a no-op (fixpoint)
    s3 = imp.import_habitkit(exp.export_habitkit(s2))
    assert sub.data_equal(s2, s3), (
        "import∘export is not idempotent on a normalized substrate"
    )


def test_export_file_is_read_only_on_state(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = imp.import_habitkit(habitkit_export())
    sub.SubstrateStore("habit-tracker").save(s)
    sub_path = tmp_path / "substrates" / "habit-tracker.json"
    mtime_before = sub_path.stat().st_mtime
    out = tmp_path / "out.json"
    doc = exp.export_file(tmp_path, out)
    assert out.exists()
    assert len(doc["habits"]) == 5
    # exporter must not have rewritten the substrate file
    assert sub_path.stat().st_mtime == mtime_before

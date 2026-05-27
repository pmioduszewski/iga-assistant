"""The sanctioned record entry point (Wave B): mutation correctness, idempotency,
isolation, privacy, and the click→record→re-project→stats round trip.

THE round-trip assertion that gates Definition-of-Done item 2 is
``test_click_record_reproject_updates_grid_and_streak_via_stats`` — it proves
a "click" relayed to the entry point updates the GRID and the STREAK exactly as the
frozen ``stats.py`` computes them, with zero habit logic in the entry point itself.

Privacy/isolation (binding): every test is synthetic-only and runs under an
``IGA_STATE_DIR`` tmp root; ``test_record_refuses_without_state_dir`` proves
the CLI has NO implicit real-state default;
``test_record_never_touches_real_state`` proves the real ~/Gaia/state
substrate + both widget JSONs are byte/mtime-unchanged across a full run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from _engine import import_habitkit as imp
from _engine import record as rec
from _engine import stats as st
from _engine import substrate as sub
from _engine import widget_projection as wp
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)
_ENGINE = Path(__file__).resolve().parents[1] / "engine"


def _seed(tmp_path) -> sub.Substrate:
    """Import the synthetic export into the isolated state root and return
    the freshly-loaded substrate."""
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")
    imp.import_file(src, tmp_path)
    return sub.SubstrateStore("habit-tracker").load()


# --------------------------------------------------------------------------- #
# 1. pure mutation: add / remove / set-amount + idempotency
# --------------------------------------------------------------------------- #
def test_apply_add_creates_one_canonical_completion():
    s = imp.import_habitkit(habitkit_export())
    # Gym has NO completion on 2026-05-16 in the fixture.
    s, r = rec.apply_record(
        s, entity_id="h-gym", day="2026-05-16", op="add"
    )
    evs = [e for e in s.events
           if e.entity_id == "h-gym" and e.date == "2026-05-16"]
    assert len(evs) == 1
    assert evs[0].amount == 1
    assert evs[0].id == rec.entrypoint_event_id("h-gym", "2026-05-16")
    assert r["previous_amount"] == 0 and r["amount"] == 1
    assert r["changed"] is True and r["deleted"] is False


def test_apply_add_is_idempotent_per_day_increment():
    s = imp.import_habitkit(habitkit_export())
    s, _ = rec.apply_record(s, entity_id="h-gym", day="2026-05-16", op="add")
    s, r2 = rec.apply_record(s, entity_id="h-gym", day="2026-05-16", op="add")
    evs = [e for e in s.events
           if e.entity_id == "h-gym" and e.date == "2026-05-16"]
    # still exactly ONE event for the day (no duplicate), amount climbed 1->2
    assert len(evs) == 1
    assert evs[0].amount == 2
    assert r2["previous_amount"] == 1 and r2["amount"] == 2


def test_apply_remove_deletes_the_day_and_is_noop_when_absent():
    s = imp.import_habitkit(habitkit_export())
    # Reading IS done 2026-05-16 in the fixture; remove clears it.
    s, r = rec.apply_record(
        s, entity_id="h-reading", day="2026-05-16", op="remove"
    )
    evs = [e for e in s.events
           if e.entity_id == "h-reading" and e.date == "2026-05-16"]
    assert evs == []
    assert r["deleted"] is True and r["changed"] is True
    # remove again -> no-op (idempotent), still absent
    s, r2 = rec.apply_record(
        s, entity_id="h-reading", day="2026-05-16", op="remove"
    )
    assert r2["changed"] is False and r2["previous_amount"] == 0


def test_apply_set_amount_is_exact_and_idempotent():
    s = imp.import_habitkit(habitkit_export())
    s, r = rec.apply_record(
        s, entity_id="h-gym", day="2026-05-20", op="set", set_amount=5
    )
    ev = [e for e in s.events
          if e.entity_id == "h-gym" and e.date == "2026-05-20"][0]
    assert ev.amount == 5 and r["amount"] == 5
    # set to the same value -> reported no-op, still one event amount 5
    s, r2 = rec.apply_record(
        s, entity_id="h-gym", day="2026-05-20", op="set", set_amount=5
    )
    assert r2["changed"] is False
    evs = [e for e in s.events
           if e.entity_id == "h-gym" and e.date == "2026-05-20"]
    assert len(evs) == 1 and evs[0].amount == 5
    # set to 0 deletes the day (explicit zero == not done)
    s, r3 = rec.apply_record(
        s, entity_id="h-gym", day="2026-05-20", op="set", set_amount=0
    )
    assert r3["deleted"] is True
    assert not [e for e in s.events
                if e.entity_id == "h-gym" and e.date == "2026-05-20"]


def test_apply_collapses_a_multicompletion_day_to_one_event():
    """An imported day with several raw completions (Reading 05-16 has the
    normal one + the tz-edge one) collapses to the entry point's single canonical
    Event on the next mutation, preserving the SUM as the base amount."""
    s = imp.import_habitkit(habitkit_export())
    before = [e for e in s.events
              if e.entity_id == "h-reading" and e.date == "2026-05-16"]
    assert len(before) == 2  # c-r3 + c-r-tz both land on civil 2026-05-16
    s, r = rec.apply_record(
        s, entity_id="h-reading", day="2026-05-16", op="add"
    )
    after = [e for e in s.events
             if e.entity_id == "h-reading" and e.date == "2026-05-16"]
    assert len(after) == 1                 # collapsed
    assert r["previous_amount"] == 2       # 1 + 1 summed (same as stats)
    assert after[0].amount == 3            # max(1, 2+1)


def test_apply_unknown_entity_and_bad_amount_raise():
    s = imp.import_habitkit(habitkit_export())
    import pytest

    with pytest.raises(rec.RecordError):
        rec.apply_record(s, entity_id="nope", day="2026-05-16", op="add")
    with pytest.raises(rec.RecordError):
        rec.apply_record(
            s, entity_id="h-gym", day="2026-05-16", op="set",
            set_amount=-1,
        )
    with pytest.raises(rec.RecordError):
        rec.apply_record(
            s, entity_id="h-gym", day="not-a-date", op="add"
        )


# --------------------------------------------------------------------------- #
# 2. THE round trip: click -> record -> re-project -> stats agree (DoD #2)
# --------------------------------------------------------------------------- #
def test_click_record_reproject_updates_grid_and_streak_via_stats(
    tmp_path, monkeypatch
):
    """A 'click' relayed to the entry point must update BOTH the projected grid and
    the streak EXACTLY as the frozen stats.py computes them — proving the
    Swift side needs zero habit logic. The entry point adds the only missing day in
    Gym's current streak window; stats.current_streak is the oracle."""
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)

    # Gym (week goal 3/wk; per-day threshold defaults to 1). Build a run of
    # consecutive done-days ending TODAY by clicking each day's square.
    days = [
        "2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16",
    ]
    for d in days:
        out = rec.record(
            state_dir=tmp_path, entity_id="h-gym", day=d, op="add",
            window_days=30,
        )
        assert out["changed"] in (True, False)

    # ---- streak oracle: stats.py on the persisted substrate ----
    s = sub.SubstrateStore("habit-tracker").load()
    hs = st.habit_stats(s, "h-gym", today=TODAY)
    # 2026-05-12 also has a fixture completion (c-g1) -> the streak is the
    # consecutive run ...05-12,13,14,15,16 = 5 days. The entry point did NOT compute
    # this; stats.py did. Assert the entry point-driven substrate yields it.
    assert hs.current_streak == 5, (
        f"stats.py streak after clicks = {hs.current_streak}, expected 5"
    )

    # ---- grid oracle: the re-emitted Wave-B widget JSON ----
    habits_json = json.loads(
        wp.habits_widget_data_path().read_text(encoding="utf-8")
    )
    assert habits_json["schema_version"] == 2
    gym = next(h for h in habits_json["data"]["habits"]
               if h["id"] == "h-gym")
    # The widget's streak number is whatever stats.py computed — identical.
    assert gym["current_streak"] == hs.current_streak
    assert gym["longest_streak"] == hs.longest_streak
    by_date = {c["date"]: c["level"] for c in gym["cells"]}
    for d in days:
        assert by_date[d] >= 1, f"clicked day {d} must be lit in the grid"

    # ---- the legacy v1 file is ALSO refreshed and still valid ----
    v1 = json.loads(
        wp._producer.widget_data_path().read_text(encoding="utf-8")
    )
    assert v1["schema_version"] == 1
    assert v1["type"] == "contribution-grid"


def test_record_remove_unlights_grid_and_breaks_streak(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    # Reading is a 1/day habit done 14,15,16 in the fixture -> streak 3.
    s0 = sub.SubstrateStore("habit-tracker").load()
    assert st.habit_stats(s0, "h-reading", today=TODAY).current_streak == 3
    # Click 2026-05-15's square OFF.
    rec.record(
        state_dir=tmp_path, entity_id="h-reading", day="2026-05-15",
        op="remove", window_days=30,
    )
    s1 = sub.SubstrateStore("habit-tracker").load()
    hs = st.habit_stats(s1, "h-reading", today=TODAY)
    # 05-15 now not-done -> the streak ending today is only 14? no: 16,15,14
    # broken at 15 -> current streak counts back from today: 16 ok, 15 fail
    # => streak == 1 (just today).
    assert hs.current_streak == 1
    habits = json.loads(
        wp.habits_widget_data_path().read_text(encoding="utf-8")
    )
    r = next(h for h in habits["data"]["habits"] if h["id"] == "h-reading")
    by_date = {c["date"]: c["level"] for c in r["cells"]}
    assert by_date["2026-05-15"] == 0
    assert r["current_streak"] == 1


# --------------------------------------------------------------------------- #
# 3. round-trip fixpoint still holds after a entry point mutation
# --------------------------------------------------------------------------- #
def test_entrypoint_authored_day_roundtrips_as_fixpoint():
    """A entry point-authored day is a natively-authored Event (no hk_* provenance).
    Per the documented contract (test_roundtrip ::
    test_native_substrate_roundtrip_is_idempotent_normalizer) the FIRST round
    trip legitimately *adds* synthesized HabitKit provenance attrs so the
    export is a valid HabitKit file; the binding guarantee is domain-field
    preservation + import∘export being an idempotent normalizer (a fixpoint
    after the first pass). The entry point must not weaken that."""
    from _engine import export_habitkit as exp

    s = imp.import_habitkit(habitkit_export())
    s, _ = rec.apply_record(
        s, entity_id="h-gym", day="2026-06-01", op="set", set_amount=2
    )
    s2 = imp.import_habitkit(exp.export_habitkit(s))
    # domain fields of the entry point-authored day survive the round trip exactly
    ev = next(
        e for e in s2.events
        if e.entity_id == "h-gym" and e.date == "2026-06-01"
    )
    assert ev.amount == 2 and ev.date == "2026-06-01"
    assert ev.id == rec.entrypoint_event_id("h-gym", "2026-06-01")
    # idempotent normalizer: a second round trip is a no-op (true fixpoint)
    s3 = imp.import_habitkit(exp.export_habitkit(s2))
    assert sub.data_equal(s2, s3), (
        "entry point mutation broke the import∘export fixpoint"
    )


# --------------------------------------------------------------------------- #
# 4. isolation + privacy guard (synthetic only; real state untouched)
# --------------------------------------------------------------------------- #
def _real_state_root() -> Path:
    return Path.home() / "Iga" / "state"


def test_record_refuses_without_state_dir():
    """The CLI MUST have no implicit real-state default — running it without
    --state-dir is an argparse error (exit 2), never a write to live data."""
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "record.py"),
         "--habit", "h-gym", "--date", "2026-05-16", "--add"],
        capture_output=True, text=True,
    )
    assert p.returncode != 0
    assert "--state-dir" in (p.stderr + p.stdout)
    # the programmatic entry point also refuses an empty state_dir
    import pytest

    with pytest.raises(rec.RecordError):
        rec.record(state_dir="", entity_id="h-gym", day="2026-05-16",
                   op="add")


def test_record_cli_isolated_roundtrip(tmp_path):
    """Drive the CLI exactly as the app's relay entry point would, fully isolated."""
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")
    imp.import_file(src, tmp_path)
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "record.py"),
         "--state-dir", str(tmp_path),
         "--habit", "h-gym", "--date", "2026-05-16", "--add",
         "--days", "30"],
        capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "recorded:" in p.stdout
    # both widget files were re-emitted under the ISOLATION root only
    assert (tmp_path / "widgets" / "habit-tracker-habit-grid.json").exists()
    assert (tmp_path / "widgets" / "habit-tracker-habits.json").exists()
    assert (tmp_path / "substrates" / "habit-tracker.json").exists()


def test_record_never_touches_real_state(tmp_path, monkeypatch):
    """A full entry point run (CLI + programmatic) must leave the user's REAL
    ~/Gaia/state substrate AND both widget JSONs byte/mtime-unchanged."""
    real_root = _real_state_root()
    watched = [
        real_root / "substrates" / "habit-tracker.json",
        real_root / "widgets" / "habit-tracker-habit-grid.json",
        real_root / "widgets" / "habit-tracker-habits.json",
    ]

    def snap(p: Path):
        if p.exists():
            return (True, p.stat().st_mtime, p.read_bytes())
        return (False, None, None)

    before = {p: snap(p) for p in watched}

    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    rec.record(state_dir=tmp_path, entity_id="h-gym",
               day="2026-05-16", op="add", window_days=30)
    rec.record(state_dir=tmp_path, entity_id="h-reading",
               day="2026-05-15", op="set", set_amount=0)

    for p in watched:
        existed, mtime, data = before[p]
        if existed:
            assert p.exists(), f"{p}: real file deleted by the entry point"
            assert p.stat().st_mtime == mtime, (
                f"{p}: REAL ~/Gaia/state mtime changed — entry point wrote live data"
            )
            assert p.read_bytes() == data, (
                f"{p}: REAL ~/Gaia/state bytes changed — data loss"
            )
        else:
            assert not p.exists(), (
                f"{p}: entry point created a file under the real ~/Gaia/state "
                f"despite IGA_STATE_DIR isolation"
            )


def test_reproject_is_non_mutating_and_advances_today(
    tmp_path, monkeypatch
):
    """`--reproject` re-emits BOTH widget files from the CURRENT substrate
    with the system `today`, WITHOUT loading-mutating-saving the substrate.
    This is the cold-launch staleness fix: a Mac-restart-launched app with a
    day-stale widget gets the engine numbers refreshed via a NON-mutating
    nudge (the substrate file is byte-identical before/after)."""
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    # Force a genuinely day-stale widget: rewrite `today` + truncate cells.
    hb = tmp_path / "widgets" / "habit-tracker-habits.json"
    rec.record(state_dir=tmp_path, entity_id="h-gym",
               day="2026-05-10", op="add", window_days=120)
    doc = json.loads(hb.read_text())
    doc["today"] = "2026-05-10"
    for h in doc["data"]["habits"]:
        h["cells"] = [c for c in h["cells"] if c["date"] <= "2026-05-10"]
    hb.write_text(json.dumps(doc))

    subp = tmp_path / "substrates" / "habit-tracker.json"
    before = subp.read_bytes()

    res = rec.reproject(state_dir=tmp_path, window_days=120)

    assert res["reprojected"] is True
    assert subp.read_bytes() == before, (
        "reproject MUST leave the substrate byte-identical (non-mutating)"
    )
    from datetime import datetime, timezone

    sys_today = datetime.now(timezone.utc).date().isoformat()
    doc2 = json.loads(hb.read_text())
    assert doc2["today"] == sys_today, (
        "reproject must advance a day-stale widget to the system date"
    )
    assert doc2["data"]["habits"][0]["cells"][-1]["date"] == sys_today


def test_reproject_cli_rejects_mutation_flags(tmp_path, monkeypatch):
    """The CLI guard: `--reproject` is non-mutating by construction —
    passing any mutation flag with it is a hard error (exit 2), so a
    careless caller can never sneak a write through the refresh path."""
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(_ENGINE / "record.py"),
         "--state-dir", str(tmp_path), "--reproject",
         "--habit", "h-gym", "--date", "2026-05-16", "--add"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2, proc.stderr
    assert "non-mutating" in proc.stderr
    # And a bare mutation with no op / no habit is also a clean usage error,
    # not a traceback (the restructured arg-parsing must stay friendly).
    proc2 = subprocess.run(
        [sys.executable, str(_ENGINE / "record.py"),
         "--state-dir", str(tmp_path), "--habit", "h-gym"],
        capture_output=True, text=True,
    )
    assert proc2.returncode == 2
    assert "requires --habit, --date" in proc2.stderr


def test_no_engine_source_references_real_export_path():
    """Privacy: the Wave-B engine sources (record.py + the widget_projection
    additions) must NOT hard-reference the real HabitKit export or a real
    Downloads path (mirrors the Wave-A privacy guard for the new code).
    Tokens are split so this guard never trips on its own source."""
    bad1 = "habitkit_" + "export.json"
    bad2 = "Downloads/" + "habitkit"
    eng = Path(__file__).resolve().parents[1] / "engine"
    for f in ("record.py", "widget_projection.py"):
        txt = (eng / f).read_text(encoding="utf-8")
        assert bad1 not in txt, f"{f} references the real export"
        assert bad2 not in txt, f"{f} references a real Downloads path"

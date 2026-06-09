"""The sanctioned management entry point (Wave D): rename / delete / set-goal /
import / export correctness, idempotency, isolation, privacy, and the
substrate-mutation → re-project contract.

Privacy/isolation (binding): every test is synthetic-only and runs under an
``IGA_STATE_DIR`` tmp root. ``test_manage_refuses_without_state_dir`` proves
the entry point has NO implicit real-state default; ``test_export_is_pure_read``
proves an export never mutates the substrate; the source-scan guard proves
the entry point never hard-references the real HabitKit export path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from _engine import export_habitkit as exp
from _engine import import_habitkit as imp
from _engine import manage as mng
from _engine import substrate as sub
from _engine import widget_projection as wp
from _synthetic import habitkit_export

TODAY = date(2026, 5, 16)
_ENGINE = Path(__file__).resolve().parents[1] / "engine"


def _seed(tmp_path) -> sub.Substrate:
    src = tmp_path / "export.json"
    src.write_text(json.dumps(habitkit_export()), encoding="utf-8")
    imp.import_file(src, tmp_path)
    return sub.SubstrateStore("habit-tracker").load()


def _any_habit_id(s: sub.Substrate) -> str:
    return sorted(s.entities, key=lambda e: (e.order_index, e.id))[0].id


# --------------------------------------------------------------------------- #
# 1. pure rename
# --------------------------------------------------------------------------- #
def test_rename_changes_name_and_is_a_noop_when_identical():
    s = imp.import_habitkit(habitkit_export())
    hid = _any_habit_id(s)
    s, r = mng.apply_rename(s, entity_id=hid, name="Brand New Name")
    assert s.entity(hid).name == "Brand New Name"
    assert r["changed"] is True
    s, r2 = mng.apply_rename(s, entity_id=hid, name="Brand New Name")
    assert r2["changed"] is False  # idempotent
    assert s.entity(hid).name == "Brand New Name"


def test_rename_rejects_unknown_and_empty():
    s = imp.import_habitkit(habitkit_export())
    with pytest.raises(mng.ManageError):
        mng.apply_rename(s, entity_id="nope", name="X")
    with pytest.raises(mng.ManageError):
        mng.apply_rename(
            s, entity_id=_any_habit_id(s), name="   ")


# --------------------------------------------------------------------------- #
# 2. pure delete + cascade
# --------------------------------------------------------------------------- #
def test_delete_cascades_all_references_and_is_idempotent():
    s = imp.import_habitkit(habitkit_export())
    # pick a habit that actually has events + an interval to prove cascade
    hid = next(
        e.id for e in s.entities
        if s.events_for(e.id) and s.intervals_for(e.id)
    )
    assert s.events_for(hid) and s.intervals_for(hid)
    s, r = mng.apply_delete(s, entity_id=hid)
    assert r["deleted"] is True and r["changed"] is True
    assert s.entity(hid) is None
    assert s.events_for(hid) == []
    assert s.intervals_for(hid) == []
    assert all(m.entity_id != hid for m in s.mappings)
    assert all(rm.entity_id != hid for rm in s.reminders)
    # deleting again: same end-state, reported no-op (not an error)
    s, r2 = mng.apply_delete(s, entity_id=hid)
    assert r2["deleted"] is False and r2["changed"] is False


def test_delete_does_not_touch_other_habits():
    s = imp.import_habitkit(habitkit_export())
    ids = [e.id for e in s.entities]
    victim, survivor = ids[0], ids[1]
    surv_events_before = len(s.events_for(survivor))
    s, _ = mng.apply_delete(s, entity_id=victim)
    assert s.entity(survivor) is not None
    assert len(s.events_for(survivor)) == surv_events_before


# --------------------------------------------------------------------------- #
# 3. pure set-goal
# --------------------------------------------------------------------------- #
def test_set_goal_replaces_active_interval_with_stable_id_and_fixpoint():
    s = imp.import_habitkit(habitkit_export())
    hid = _any_habit_id(s)
    s, r = mng.apply_set_goal(
        s, entity_id=hid, period="day", target=None,
        per_day_target=50, allow_exceed=True, today=TODAY,
    )
    active = [g for g in s.intervals_for(hid) if g.end is None]
    assert len(active) == 1
    g = active[0]
    assert g.id == f"goal-{hid}-2026-05-16"      # deterministic
    assert g.period == "day" and g.per_day_target == 50
    assert r["changed"] is True
    # Re-applying the identical goal on the same day is a FIXPOINT.
    s, r2 = mng.apply_set_goal(
        s, entity_id=hid, period="day", target=None,
        per_day_target=50, allow_exceed=True, today=TODAY,
    )
    active2 = [g for g in s.intervals_for(hid) if g.end is None]
    assert len(active2) == 1 and active2[0].id == g.id
    assert r2["changed"] is False               # no-op


def test_set_goal_none_means_no_active_interval():
    s = imp.import_habitkit(habitkit_export())
    hid = _any_habit_id(s)
    mng.apply_set_goal(
        s, entity_id=hid, period="week", target=3,
        per_day_target=None, allow_exceed=True, today=TODAY)
    s, r = mng.apply_set_goal(
        s, entity_id=hid, period="none", target=None,
        per_day_target=None, allow_exceed=True, today=TODAY)
    assert [g for g in s.intervals_for(hid) if g.end is None] == []
    assert r["period"] == "none"


def test_reorder_moves_habit_and_renumbers_contiguously():
    s = imp.import_habitkit(habitkit_export())
    active0 = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id))
    order0 = [e.id for e in active0]
    assert len(order0) >= 3
    mover = order0[0]
    s, r = mng.apply_reorder(s, entity_id=mover, position=3)
    assert r["op"] == "reorder" and r["changed"] is True
    assert r["position"] == 3 and r["count"] == len(order0)
    active1 = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id))
    order1 = [e.id for e in active1]
    # Landed exactly at slot 3 (1-based → index 2).
    assert order1.index(mover) == 2
    # The others kept their relative order, minus the mover.
    assert [i for i in order1 if i != mover] == \
        [i for i in order0 if i != mover]
    # order_index is contiguous 0..n-1 with NO collision.
    idxs = sorted(e.order_index for e in active1)
    assert idxs == list(range(len(active1)))


def test_reorder_is_a_fixpoint_and_clamps_out_of_range():
    s = imp.import_habitkit(habitkit_export())
    active = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id))
    first = active[0].id
    # First call normalizes order_index to contiguous 0-based (the source
    # import's indices may be sparse) — that legitimately "changed" bytes.
    s, _ = mng.apply_reorder(s, entity_id=first, position=2)
    # Re-applying the IDENTICAL position is now a true fixpoint: no change.
    s, r2 = mng.apply_reorder(s, entity_id=first, position=2)
    assert r2["changed"] is False, "repeat reorder must be a no-op"
    # Clamp: 0 → 1, huge → count (never raises / never out of range).
    _s, r0 = mng.apply_reorder(s, entity_id=first, position=0)
    assert r0["position"] == 1
    _s, rN = mng.apply_reorder(s, entity_id=first, position=9999)
    assert rN["position"] == rN["count"]


def test_reorder_rejects_unknown_and_archived():
    s = imp.import_habitkit(habitkit_export())
    with pytest.raises(mng.ManageError):
        mng.apply_reorder(s, entity_id="ghost", position=1)
    archived = next(
        (e for e in s.entities if e.archived), None)
    if archived is not None:
        with pytest.raises(mng.ManageError):
            mng.apply_reorder(
                s, entity_id=archived.id, position=1)


def test_manage_reorder_flows_through_to_widget_order(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    active = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id))
    mover = active[0].id
    mng.manage(state_dir=tmp_path, op="reorder",
               entity_id=mover, position=2, window_days=30)
    doc = json.loads(
        (tmp_path / "widgets" / "habit-tracker-habits.json").read_text())
    ids = [h["id"] for h in doc["data"]["habits"]]
    assert ids.index(mover) == 1, (
        "reorder must flow through to the widget the app renders"
    )


def test_cli_set_order_requires_habit(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "manage.py"),
         "--state-dir", str(tmp_path), "--set-order", "2"],
        capture_output=True, text=True)
    assert p.returncode == 2
    assert "requires --habit" in p.stderr


def test_set_goal_rejects_bad_period_and_target():
    s = imp.import_habitkit(habitkit_export())
    hid = _any_habit_id(s)
    with pytest.raises(mng.ManageError):
        mng.apply_set_goal(
            s, entity_id=hid, period="biweekly", target=None,
            per_day_target=None, allow_exceed=True, today=TODAY)
    with pytest.raises(mng.ManageError):
        mng.apply_set_goal(
            s, entity_id=hid, period="day", target=0,
            per_day_target=None, allow_exceed=True, today=TODAY)
    with pytest.raises(mng.ManageError):
        mng.apply_set_goal(
            s, entity_id="ghost", period="day", target=None,
            per_day_target=2, allow_exceed=True, today=TODAY)


# --------------------------------------------------------------------------- #
# 4. the I/O entry point: persist + re-emit; export is a pure read
# --------------------------------------------------------------------------- #
def test_manage_rename_persists_and_reemits_widget(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    hid = _any_habit_id(s)
    res = mng.manage(
        state_dir=tmp_path, op="rename",
        entity_id=hid, name="Renamed In EntryPoint", window_days=120)
    assert Path(res["habits_widget_path"]).exists()
    reloaded = sub.SubstrateStore("habit-tracker").load()
    assert reloaded.entity(hid).name == "Renamed In EntryPoint"
    doc = json.loads(Path(res["habits_widget_path"]).read_text())
    names = {h["name"] for h in doc["data"]["habits"]}
    # archived habits are excluded from the widget; the renamed (active)
    # one must be reflected there.
    assert "Renamed In EntryPoint" in names


def test_manage_set_goal_then_widget_shows_per_day_target(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    hid = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id))[0].id
    mng.manage(
        state_dir=tmp_path, op="set-goal", entity_id=hid,
        period="day", per_day_target=50, today=TODAY, window_days=30)
    doc = json.loads(
        (tmp_path / "widgets" / "habit-tracker-habits.json").read_text())
    h = next(h for h in doc["data"]["habits"] if h["id"] == hid)
    assert h["goal"]["per_day_target"] == 50, (
        "set-goal must flow through to the widget the app renders"
    )


def test_export_is_pure_read_then_import_round_trips(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    subp = tmp_path / "substrates" / "habit-tracker.json"
    before = subp.read_bytes()
    out = tmp_path / "exported.json"
    res = mng.manage(
        state_dir=tmp_path, op="export", path=out)
    assert res["changed"] is False
    assert out.exists()
    assert subp.read_bytes() == before, (
        "export MUST NOT mutate the substrate (pure read)"
    )
    # Re-importing our own export into a FRESH root reproduces the
    # substrate (the frozen importer's fixpoint, exercised via the entry point).
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("IGA_STATE_DIR", str(fresh))
    mng.manage(state_dir=fresh, op="import", path=out, window_days=30)
    a = sub.from_doc(json.loads(before.decode()))
    b = sub.SubstrateStore("habit-tracker").load()
    assert sub.data_equal(a, b), "export→import must be a fixpoint"


# --------------------------------------------------------------------------- #
# 5. isolation / privacy / CLI
# --------------------------------------------------------------------------- #
def test_manage_refuses_without_state_dir():
    with pytest.raises(mng.ManageError):
        mng.manage(state_dir="", op="delete", entity_id="x")


def test_cli_requires_habit_for_targeted_ops(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "manage.py"),
         "--state-dir", str(tmp_path), "--delete"],
        capture_output=True, text=True)
    assert p.returncode == 2
    assert "requires --habit" in p.stderr


def test_cli_export_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    out = tmp_path / "cli-export.json"
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "manage.py"),
         "--state-dir", str(tmp_path), "--export", str(out)],
        capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert out.exists()
    doc = json.loads(out.read_text())
    assert "habits" in doc and "completions" in doc


def test_manage_never_touches_real_state(tmp_path, monkeypatch):
    """The real ~/Iga/state substrate + widget JSONs must be byte/mtime
    unchanged across rename + delete + set-goal under an isolated root."""
    real = Path.home() / "Iga" / "state"
    watched = [
        real / "substrates" / "habit-tracker.json",
        real / "widgets" / "habit-tracker-habits.json",
        real / "widgets" / "habit-tracker-habit-grid.json",
    ]
    snap = {
        p: (p.exists(),
            p.stat().st_mtime if p.exists() else None,
            p.read_bytes() if p.exists() else None)
        for p in watched
    }
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    ids = [e.id for e in s.entities]
    mng.manage(state_dir=tmp_path, op="rename",
               entity_id=ids[0], name="Z", window_days=30)
    mng.manage(state_dir=tmp_path, op="set-goal", entity_id=ids[1],
               period="day", per_day_target=10, today=TODAY,
               window_days=30)
    mng.manage(state_dir=tmp_path, op="delete",
               entity_id=ids[2], window_days=30)
    for p in watched:
        existed, mtime, datab = snap[p]
        if existed:
            assert p.exists() and p.stat().st_mtime == mtime, (
                f"{p}: REAL ~/Iga/state changed — isolation breach"
            )
            assert p.read_bytes() == datab
        else:
            assert not p.exists(), f"{p}: entry point created a real-state file"


def test_no_engine_source_references_real_export_path():
    """Privacy: manage.py must NOT hard-reference the real HabitKit export
    or a real Downloads path (mirrors the Wave-A/B guard for new code)."""
    bad1 = "habitkit_" + "export.json"
    bad2 = "Downloads/" + "habitkit"
    txt = (_ENGINE / "manage.py").read_text(encoding="utf-8")
    assert bad1 not in txt
    assert bad2 not in txt


# --------------------------------------------------------------------------- #
# archive (graduate) + set-color
# --------------------------------------------------------------------------- #
def test_apply_archive_flips_flag_idempotent_and_keeps_history():
    s = imp.import_habitkit(habitkit_export())
    hid = next(e.id for e in s.entities
               if not e.archived and s.events_for(e.id))
    ev_before = len(s.events_for(hid))
    s, r = mng.apply_archive(s, entity_id=hid, archived=True)
    assert r["op"] == "archive" and r["changed"] is True
    assert s.entity(hid).archived is True
    # history untouched (archive ≠ delete).
    assert len(s.events_for(hid)) == ev_before
    # idempotent: archiving again is a no-op.
    s, r2 = mng.apply_archive(s, entity_id=hid, archived=True)
    assert r2["changed"] is False
    # unarchive restores.
    s, r3 = mng.apply_archive(s, entity_id=hid, archived=False)
    assert r3["op"] == "unarchive" and s.entity(hid).archived is False
    with pytest.raises(mng.ManageError):
        mng.apply_archive(s, entity_id="ghost", archived=True)


def test_manage_archive_removes_habit_from_widget(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    hid = sorted((e for e in s.entities if not e.archived),
                 key=lambda e: (e.order_index, e.id))[0].id
    mng.manage(state_dir=tmp_path, op="archive",
               entity_id=hid, archived=True, window_days=30)
    doc = json.loads(
        (tmp_path / "widgets" / "habit-tracker-habits.json").read_text())
    assert hid not in {h["id"] for h in doc["data"]["habits"]}, (
        "archived habit must drop out of the active widget"
    )
    # active_count in the focus advisory reflects the drop.
    s2 = sub.SubstrateStore("habit-tracker").load()
    assert s2.entity(hid).archived is True


def test_apply_set_color_validates_and_normalises():
    s = imp.import_habitkit(habitkit_export())
    hid = _any_habit_id(s)
    s, r = mng.apply_set_color(s, entity_id=hid, color="#FF8800")
    assert r["op"] == "set-color" and r["changed"] is True
    assert s.entity(hid).attrs["color"] == "#ff8800"   # lowercased
    # idempotent.
    s, r2 = mng.apply_set_color(s, entity_id=hid, color="#ff8800")
    assert r2["changed"] is False
    # #rgb shorthand is accepted.
    s, _ = mng.apply_set_color(s, entity_id=hid, color="#0a0")
    assert s.entity(hid).attrs["color"] == "#0a0"
    # bad hex / unknown entity rejected.
    for bad in ("red", "ff8800", "#xyz", "#ff88", ""):
        with pytest.raises(mng.ManageError):
            mng.apply_set_color(s, entity_id=hid, color=bad)
    with pytest.raises(mng.ManageError):
        mng.apply_set_color(s, entity_id="ghost", color="#fff")


def test_manage_set_color_flows_to_widget_hex(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    s = _seed(tmp_path)
    hid = sorted((e for e in s.entities if not e.archived),
                 key=lambda e: (e.order_index, e.id))[0].id
    mng.manage(state_dir=tmp_path, op="set-color",
               entity_id=hid, color="#123abc", window_days=30)
    doc = json.loads(
        (tmp_path / "widgets" / "habit-tracker-habits.json").read_text())
    h = next(h for h in doc["data"]["habits"] if h["id"] == hid)
    # color_hex_for passes an explicit hex through verbatim.
    assert h["color"] == "#123abc"


def test_cli_archive_and_set_color_require_habit(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_STATE_DIR", str(tmp_path))
    _seed(tmp_path)
    for args in (["--archive"], ["--set-color", "#fff"]):
        p = subprocess.run(
            [sys.executable, str(_ENGINE / "manage.py"),
             "--state-dir", str(tmp_path), *args],
            capture_output=True, text=True)
        assert p.returncode == 2 and "requires --habit" in p.stderr

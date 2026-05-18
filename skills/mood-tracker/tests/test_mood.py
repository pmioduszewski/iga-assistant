"""mood-tracker: quadrant map, import field-mapping/idempotency/date,
round-trip fixpoint, deterministic stats/summary, projection shape, and
the isolation + privacy guard. Synthetic-only — never the user's data.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from _engine import (
    export_mood_csv as exp,
    import_mood_csv as imp,
    ingest as ing,
    quadrant as q,
    record as rec,
    stats as st,
    substrate as sub,
    summary as smry,
    widget_projection as wp,
)
from _synth import csv_text

TODAY = date(2026, 5, 17)
_ENGINE = Path(__file__).resolve().parents[1] / "engine"


# ---- quadrant ----------------------------------------------------------
def test_quadrant_map_and_unknown_is_neutral():
    assert q.quadrant_of("determined") == "yellow"
    assert q.quadrant_of("grateful") == "green"
    assert q.quadrant_of("anxious") == "red"
    assert q.quadrant_of("tired") == "blue"
    assert q.quadrant_of("Determined") == "yellow"      # case-insensitive
    assert q.quadrant_of("zorblax") == "unknown"
    assert q.valence_energy("grateful") == (1, -1)
    assert q.valence_energy("anxious") == (-1, 1)
    assert q.valence_energy("zorblax") == (0, 0)
    assert q.valence_level(0.8, logged=True) == 4
    assert q.valence_level(-0.8, logged=True) == 1
    assert q.valence_level(None, logged=False) == 0


def test_canonical_lexicon_is_comprehensive_and_well_formed():
    from _engine import load as _load
    lex = _load("lexicon")
    EM = lex.EMOTIONS
    # The published Mood Meter core is 100 words; we ship the core plus
    # the common expanded vocabulary — assert a substantial canonical set.
    assert len(EM) >= 130
    quads = {"yellow", "green", "red", "blue"}
    for name, (q_, desc) in EM.items():
        assert name == name.lower().strip()        # keys normalised
        assert q_ in quads                         # valid quadrant
        assert isinstance(desc, str) and 3 <= len(desc) <= 80
        assert desc.isascii()                      # OSS-clean, no glitches
    # every quadrant is well represented (not lopsided).
    from collections import Counter
    by_q = Counter(v[0] for v in EM.values())
    for k in quads:
        assert by_q[k] >= 25
    # normalised, case/space-insensitive lookup.
    assert lex.lookup("At Ease")[0] == "green"
    assert lex.lookup("  at   ease ")[0] == "green"
    assert lex.lookup("zorblax") is None
    # quadrant.py now consults the lexicon + exposes describe().
    assert q.quadrant_of("overwhelmed") == "red"   # was 'unknown' before
    assert q.quadrant_of("appreciated") == "green"
    assert q.describe("anxious") and q.describe("anxious").isascii()
    assert q.describe("zorblax") is None


def test_palette_is_hex_for_every_quadrant():
    for k in ("yellow", "green", "red", "blue", "unknown", "none"):
        v = q.PALETTE[k]
        assert v.startswith("#") and len(v) == 7
        int(v[1:], 16)                       # parses as hex
    assert q.color_of("red") == q.PALETTE["red"]
    assert q.color_of("zorblax") == q.PALETTE["unknown"]


# ---- import ------------------------------------------------------------
def test_import_maps_fields_dates_tags_and_is_idempotent():
    s = imp.import_csv(csv_text())
    assert len(s.entries) == 5
    by_emo = {e.emotion: e for e in s.entries}
    g = by_emo["Grateful"]
    assert g.date == "2026-05-17"
    assert g.ts.startswith("2026-05-17T15:39")
    assert g.quadrant == "green" and g.valence == 1 and g.energy == -1
    assert g.people == ["Family"] and g.events == ["Parenting"]
    assert g.note == "synthetic note B"
    assert "src" in g.attrs                           # lossless bag
    # unknown emotion still imported, neutral.
    z = by_emo["Zorblax"]
    assert z.quadrant == "unknown" and z.valence == 0
    # idempotent: re-import the same text → no duplicates, same data.
    s2 = imp.import_csv(csv_text(), into=s)
    assert len(s2.entries) == 5
    assert sub.data_equal(s, s2)


# ---- round-trip fixpoint ----------------------------------------------
def test_export_import_is_a_fixpoint():
    s = imp.import_csv(csv_text())
    out = exp.export_csv(s)
    s2 = imp.import_csv(out)
    assert sub.data_equal(s, s2), "import(export(S)) must equal S"
    # second round trip is also stable.
    assert exp.export_csv(s2) == out


# ---- stats / summary (deterministic) ----------------------------------
def test_stats_and_summary_are_deterministic_and_shaped():
    s = imp.import_csv(csv_text())
    a1 = st.summarize(s, today=TODAY, window_days=30)
    a2 = st.summarize(s, today=TODAY, window_days=30)
    assert a1 == a2
    assert a1["logs"] == 5
    assert a1["dominant_quadrant"] in {
        "yellow", "green", "red", "blue", "unknown"}
    assert a1["valence_mean"] is not None
    # the anxious(-1) day co-occurs with Boss/Office/Deadline.
    ctx = dict(a1["stress_context"])
    assert any(t in ctx for t in ("Boss", "Office", "Deadline"))
    md = smry.render_markdown(a1)
    assert md.startswith("**Mood — 2026-05-17**")
    assert f"trend **{a1['trend']}**" in md
    if a1["top_emotions"]:
        assert "Top:" in md


def test_summary_empty_is_graceful():
    a = st.summarize(sub.MoodSubstrate(), today=TODAY)
    assert a["logs"] == 0
    assert "no logs" in smry.render_markdown(a)


# ---- projection (the dense mood-meter-coloured Mood grid) ------------
def test_projection_is_a_valid_mood_grid():
    s = imp.import_csv(csv_text())
    w = wp.build_widget(s, today=TODAY, window_days=14)
    assert w["schema_version"] == 2
    assert w["type"] == "mood-grid"
    assert w["widget_id"] == "mood-grid"
    assert w["palette"] == q.PALETTE
    # legacy 0..4 valence cells kept for any generic reader.
    cells = w["data"]["cells"]
    assert len(cells) == 14
    for c in cells:
        assert set(c.keys()) == {"date", "level"}
        assert 0 <= c["level"] <= 4
    bydate = {c["date"]: c["level"] for c in cells}
    assert bydate["2026-05-17"] == 4   # Determined&Grateful → mean +1
    assert bydate["2026-05-16"] == 1   # Anxious(-1)
    # the new per-day dominant-quadrant cells (the dense coloured grid).
    qc = w["data"]["qcells"]
    assert len(qc) == 14
    for c in qc:
        assert set(c.keys()) == {"date", "quadrant", "color", "count"}
        assert c["color"].startswith("#")
    byq = {c["date"]: c for c in qc}
    # 2026-05-17: Determined(yellow)+Grateful(green) → tie, count 2.
    assert byq["2026-05-17"]["count"] == 2
    assert byq["2026-05-17"]["quadrant"] in {"yellow", "green"}
    assert byq["2026-05-17"]["color"] == q.color_of(
        byq["2026-05-17"]["quadrant"])
    # 2026-05-16: Anxious → red.
    assert byq["2026-05-16"]["quadrant"] == "red"
    # a day with no log is the dim "none" tile.
    nolog = next(c for c in qc if c["count"] == 0)
    assert nolog["quadrant"] == "none"
    assert nolog["color"] == q.PALETTE["none"]
    # determinism: same substrate + same today → identical (modulo the
    # wall-clock generated_at stamp).
    w2 = wp.build_widget(s, today=TODAY, window_days=14)
    w.pop("generated_at"), w2.pop("generated_at")
    assert w2 == w
    assert isinstance(w["coach"]["text"], str) and w["coach"]["text"]


# ---- recent (the "mood now ← previous" row) ---------------------------
def test_recent_is_newest_first_deterministic_and_noteless():
    s = imp.import_csv(csv_text())
    r = st.recent(s.entries, n=2)
    assert len(r) == 2
    # newest first by ts.
    assert r[0]["ts"] >= r[1]["ts"]
    for e in r:
        assert set(e.keys()) == {
            "date", "ts", "emotion", "quadrant", "parts"}
        assert "note" not in e            # privacy: never the free text
        # each ';'-joined feeling is its own part with its own quadrant.
        for p in e["parts"]:
            assert set(p.keys()) == {"emotion", "quadrant"}
            assert ";" not in p["emotion"]
    # deterministic.
    assert st.recent(s.entries, n=2) == r
    # a MULTI-feeling log → one part per ';'-joined feeling, each with
    # its OWN quadrant (primary + secondary, like the source app).
    multi = sub.MoodSubstrate()
    multi.entries = [imp._entry_from_row({
        "Date": "2026-05-17 20:00:00", "Mood": "Determined;Grateful",
        "Mood Key": "determined;grateful", "Notes": ""})]
    rm = st.recent(multi.entries, n=1)[0]
    assert [p["emotion"] for p in rm["parts"]] == ["Determined", "Grateful"]
    assert [p["quadrant"] for p in rm["parts"]] == ["yellow", "green"]
    # surfaced in the widget payload with per-part colours.
    wmulti = wp.build_widget(multi, today=TODAY, window_days=7)
    pp = wmulti["data"]["recent"][0]["parts"]
    assert [p["color"] for p in pp] == [
        q.color_of("yellow"), q.color_of("green")]
    assert all(p["color"].startswith("#") for p in pp)
    # the single-emotion case still works in the real synthetic payload.
    w = wp.build_widget(s, today=TODAY, window_days=14)
    rec = w["data"]["recent"]
    assert len(rec) == 2 and rec[0]["ts"] >= rec[1]["ts"]
    assert rec[0]["color"] == q.color_of(rec[0]["quadrant"])
    assert len(rec[0]["parts"]) == 1


# ---- isolation / privacy guard ----------------------------------------
def test_state_dir_is_mandatory_via_cli(tmp_path):
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "import_mood_csv.py"),
         "--input", str(tmp_path / "x.csv")],
        capture_output=True, text=True)
    assert p.returncode != 0  # argparse: --state-dir required


def test_pipeline_never_touches_real_state(tmp_path, monkeypatch):
    real = Path.home() / "Iga" / "state"
    watched = [
        real / "substrates" / "mood-tracker.json",
        real / "widgets" / "mood-tracker-mood.json",
    ]
    snap = {
        w: (w.exists(),
            w.stat().st_mtime if w.exists() else None,
            w.read_bytes() if w.exists() else None)
        for w in watched
    }
    src = tmp_path / "mood.csv"
    src.write_text(csv_text(), encoding="utf-8")
    counts = imp.import_file(src, tmp_path)
    assert counts["entries"] == 5
    assert (tmp_path / "substrates" / "mood-tracker.json").exists()
    out = exp.export_file(tmp_path)
    assert "Grateful" in out
    for w in watched:
        existed, mtime, data = snap[w]
        if existed:
            assert w.exists() and w.stat().st_mtime == mtime, (
                f"{w}: REAL ~/Gaia/state changed — isolation breach")
            assert w.read_bytes() == data
        else:
            assert not w.exists(), f"{w}: created under real state"


# ---- record seam (the live, one-place chat logging) -------------------
def test_record_seam_models_like_an_import_and_is_idempotent():
    s = sub.MoodSubstrate()
    row = rec.build_row(emotion="Anxious", at="2026-05-17T09:15",
                        note="before the demo", people="Boss",
                        events="Deadline")
    s, r = rec.apply_log(s, row=row)
    assert r["logs"] == 1 and r["changed"] is True
    e = s.entries[0]
    # quadrant/valence/energy are the importer's deterministic mapping.
    assert e.quadrant == "red"
    assert (e.valence, e.energy) == q.valence_energy("anxious")
    assert e.date == "2026-05-17"
    assert e.people == ["Boss"] and e.events == ["Deadline"]
    assert "src" in e.attrs                       # round-trip bag present
    # same emotion+note+minute → idempotent no-op (not a duplicate).
    s, r2 = rec.apply_log(s, row=rec.build_row(
        emotion="Anxious", at="2026-05-17T09:15", note="before the demo",
        people="Boss", events="Deadline"))
    assert r2["logs"] == 1 and r2["changed"] is False
    # a later minute the same day → a distinct log (multiple/day allowed).
    s, r3 = rec.apply_log(s, row=rec.build_row(
        emotion="Grateful", at="2026-05-17T21:40"))
    assert r3["logs"] == 2 and r3["changed"] is True
    assert {x.quadrant for x in s.entries} == {"red", "green"}


def test_record_emotion_required():
    with pytest.raises(rec.RecordError):
        rec.build_row(emotion="  ", at="2026-05-17T09:15")
    with pytest.raises(rec.RecordError):
        rec.build_row(emotion="Happy", at="not-a-date")


def test_record_entry_round_trips_like_an_import(tmp_path):
    r = rec.record(state_dir=tmp_path, emotion="Tired",
                   at="2026-05-16T23:10", note="long day",
                   places="Home office")
    assert r["quadrant"] == "blue" and r["logs"] == 1
    # the seam re-emitted the Mood widget into the isolated state.
    assert (tmp_path / "widgets" / "mood-tracker-mood.json").exists()
    # record() set IGA_STATE_DIR=tmp_path for the process; read it back.
    s = sub.MoodStore().load()
    # export → import is an exact fixpoint for seam-authored entries too
    # (the synthesized source-app row rebuilds verbatim — anti-lock-in).
    out = exp.export_csv(s)
    s2 = imp.import_csv(out)
    assert sub.data_equal(s, s2)


def test_record_state_dir_is_mandatory_via_cli():
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "record.py"),
         "--emotion", "Happy", "--at", "2026-05-17T10:00"],
        capture_output=True, text=True)
    assert p.returncode != 0  # argparse: --state-dir required


def test_record_reproject_is_non_mutating(tmp_path):
    src = tmp_path / "mood.csv"
    src.write_text(csv_text(), encoding="utf-8")
    imp.import_file(src, tmp_path)
    subj = tmp_path / "substrates" / "mood-tracker.json"
    before = subj.read_bytes()
    out = rec.reproject(state_dir=tmp_path)
    assert out["reprojected"] is True
    assert Path(out["widget_path"]).exists()
    assert subj.read_bytes() == before, "reproject must not mutate state"


def test_record_never_touches_real_state(tmp_path):
    real = Path.home() / "Iga" / "state"
    watched = [
        real / "substrates" / "mood-tracker.json",
        real / "widgets" / "mood-tracker-mood.json",
    ]
    snap = {
        w: (w.exists(),
            w.stat().st_mtime if w.exists() else None,
            w.read_bytes() if w.exists() else None)
        for w in watched
    }
    rec.record(state_dir=tmp_path, emotion="Excited",
               at="2026-05-17T08:00")
    for w in watched:
        existed, mtime, data = snap[w]
        if existed:
            assert w.exists() and w.stat().st_mtime == mtime, (
                f"{w}: REAL ~/Gaia/state changed — isolation breach")
            assert w.read_bytes() == data
        else:
            assert not w.exists(), f"{w}: created under real state"


# ---- semi-automatic ingest (the /gm-wired backfill) -------------------
def test_ingest_imports_only_when_changed(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    state = tmp_path / "state"
    exp_file = watch / "mood-export.csv"   # matches the default glob
    exp_file.write_text(csv_text(), encoding="utf-8")

    r1 = ing.ingest(state_dir=state, watch_dir=watch)
    assert r1["status"] == "imported" and r1["entries"] == 5
    assert (state / "widgets" / "mood-tracker-mood.json").exists()
    assert (state / ing.MARKER_NAME).exists()

    # unchanged file → idempotent no-op (cheap to wire into /gm daily).
    r2 = ing.ingest(state_dir=state, watch_dir=watch)
    assert r2["status"] == "unchanged"

    # a changed export → re-imported.
    exp_file.write_text(csv_text() + "\n", encoding="utf-8")
    r3 = ing.ingest(state_dir=state, watch_dir=watch)
    assert r3["status"] == "imported"


def test_ingest_no_export_is_graceful(tmp_path):
    r = ing.ingest(state_dir=tmp_path / "s", watch_dir=tmp_path / "empty")
    assert r["status"] == "no-export"


def test_ingest_state_dir_is_mandatory_via_cli():
    p = subprocess.run(
        [sys.executable, str(_ENGINE / "ingest.py")],
        capture_output=True, text=True)
    assert p.returncode != 0  # argparse: --state-dir required


def test_ingest_never_touches_real_state(tmp_path):
    real = Path.home() / "Iga" / "state"
    watched = [
        real / "substrates" / "mood-tracker.json",
        real / "widgets" / "mood-tracker-mood.json",
        real / ing.MARKER_NAME,
    ]
    snap = {
        w: (w.exists(),
            w.stat().st_mtime if w.exists() else None,
            w.read_bytes() if w.exists() else None)
        for w in watched
    }
    wd = tmp_path / "watch"
    wd.mkdir()
    (wd / "mood-export.csv").write_text(
        csv_text(), encoding="utf-8")
    ing.ingest(state_dir=tmp_path / "state", watch_dir=wd)
    for w in watched:
        existed, mtime, data = snap[w]
        if existed:
            assert w.exists() and w.stat().st_mtime == mtime, (
                f"{w}: REAL ~/Gaia/state changed — isolation breach")
            assert w.read_bytes() == data
        else:
            assert not w.exists(), f"{w}: created under real state"


def test_no_engine_source_mentions_the_source_app_brand():
    """OSS hygiene + privacy: NO engine source may literally name the
    source mood app (brand, hyphenated, abbreviation, or id-prefix
    form), nor hard-reference a real Downloads path. The search patterns
    are assembled from fragments so this test's own source stays
    brand-free too."""
    import re
    # built from pieces → the brand never appears verbatim in this file
    brand = re.compile("h" + "ow" + r"[ \-]?" + "we" + r"[ \-]?" + "feel",
                        re.IGNORECASE)
    abbr = re.compile(r"\b" + "h" + "w" + "f" + r"\b", re.IGNORECASE)
    idpfx = "h" + "wf-"
    dl = "Downloads" + "/" + "h" + "wf"
    for f in _ENGINE.glob("*.py"):
        t = f.read_text(encoding="utf-8")
        assert not brand.search(t), f"{f.name} names the source app"
        assert not abbr.search(t), f"{f.name} uses the brand abbreviation"
        assert idpfx not in t.lower(), f"{f.name} uses the brand id-prefix"
        assert dl not in t, f"{f.name} references a real Downloads path"

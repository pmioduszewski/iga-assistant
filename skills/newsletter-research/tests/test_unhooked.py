"""Unhooked-cluster offer detector tests — no Gmail, no MCP, no network.

Asserts the three contract-critical properties:
  1. PII-free: no raw sender/domain/label/subject ever appears in the
     detection, the offer, or the persisted state file.
  2. Generic: coverage is computed from the user's actual rules/hooks/*.md.
  3. Killswitch-respecting: IGA_PROACTIVE_RESEARCH=0 / IGA_PROACTIVE_SPAWN=0
     behave exactly like the producer / consumer.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "engine"))

import unhooked  # type: ignore  # noqa: E402
from unhooked import build_offer, detect, run  # type: ignore  # noqa: E402

NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)


def _write_hook(d: Path, name: str, label: str):
    (d / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: test {name}\n"
        "trigger:\n"
        f'  gmail_label: "{label}"\n'
        "interest_profile: |\n"
        "  x\n"
        "scoring_context:\n"
        '  - "projects/*"\n'
        f'output_wing: "vault/{name}"\n'
        "---\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# coverage is generic + correct
# --------------------------------------------------------------------------- #
def test_covered_streams_excluded(tmp_path):
    _write_hook(tmp_path, "dev", "Newsletter/Dev")
    counts = {
        "newsletter/dev": 20,        # covered by the hook → excluded
        "pointer.io": 9,             # unhooked
        "tldr.tech": 7,              # unhooked
        "bytesized.dev": 5,          # unhooked
    }
    det = detect(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: counts,
    )
    assert det["unhooked_streams"] == 3
    assert det["unhooked_messages"] == 21
    assert det["threshold_met"] is True


def test_below_threshold_no_offer(tmp_path):
    det = detect(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"a.com": 2, "b.com": 1},
    )
    assert det["threshold_met"] is False
    assert build_offer(det) is None


# --------------------------------------------------------------------------- #
# PII contract — nothing raw survives
# --------------------------------------------------------------------------- #
def test_no_pii_in_detection_or_offer(tmp_path):
    raw_ids = {"secret-sender@acme-corp.com": 10, "private.list.io": 9}
    det = detect(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: raw_ids,
    )
    offer = build_offer(det)
    blob = json.dumps(det) + json.dumps(offer)
    for raw in raw_ids:
        assert raw not in blob
        assert raw.split("@")[0] not in blob
    # Only hashed keys + counts present.
    for c in det["clusters"]:
        assert len(c["key"]) == 12
        assert isinstance(c["count"], int)


def test_offer_is_generic_counts_only(tmp_path):
    det = detect(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"x.io": 8, "y.io": 8, "z.io": 8},
    )
    offer = build_offer(det)
    assert offer is not None
    assert offer["kind"] == "newsletter-unhooked-offer"
    assert offer["deliver"] == "surface_next_brief"
    assert "3 streams" in offer["headline"]
    assert len(offer["question"]["options"]) == 2
    assert "Recommended" in offer["question"]["options"][0]["label"]


# --------------------------------------------------------------------------- #
# exactly one offer; idempotent; clears under threshold
# --------------------------------------------------------------------------- #
def test_run_writes_single_offer_then_clears(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    state = tmp_path / "offer.json"

    r1 = run(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"a.io": 8, "b.io": 8, "c.io": 8},
        state_path=state,
    )
    assert r1["wrote_state"] is True
    assert state.is_file()
    fp1 = json.loads(state.read_text())["fingerprint"]

    # Same gap → same fingerprint (idempotent, no spam).
    run(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"a.io": 8, "b.io": 8, "c.io": 8},
        state_path=state,
    )
    assert json.loads(state.read_text())["fingerprint"] == fp1

    # Gap closes → stale offer cleared, back to dormant.
    r3 = run(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {},
        state_path=state,
    )
    assert r3["offer"] is None
    assert not state.is_file()


def test_killswitch_research_off(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "0")
    state = tmp_path / "offer.json"
    r = run(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"a.io": 99, "b.io": 99, "c.io": 99},
        state_path=state,
    )
    assert r["killswitched"] is True
    assert not state.is_file()


def test_spawn_disabled_detects_but_writes_no_state(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.setenv("IGA_PROACTIVE_SPAWN", "0")
    state = tmp_path / "offer.json"
    r = run(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        stream_counts=lambda d: {"a.io": 8, "b.io": 8, "c.io": 8},
        state_path=state,
    )
    assert r["offer"] is not None       # detected
    assert r["wrote_state"] is False    # but mutated nothing
    assert not state.is_file()


def test_no_gmail_wired_no_offer(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    state = tmp_path / "offer.json"
    r = run(now=NOW, hooks_glob=str(tmp_path / "*.md"), state_path=state)
    assert r["offer"] is None
    assert not state.is_file()

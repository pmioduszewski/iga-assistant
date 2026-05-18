"""Producer tests — fully mocked I/O (no Gmail, no MCP, no network).

Mirrors skills/iga-proactive-research/tests/test_scanner.py mocking style:
a types.SimpleNamespace fake for MemPalace, an injected gmail_search, and a
real (temp-file) frozen ledger/governor so the anti-duplicate + budget gates
are exercised for real against a disposable db.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "engine"))
ENGINE = SKILL_ROOT.parent / "iga-proactive" / "engine"
sys.path.insert(0, str(ENGINE))

import producer  # type: ignore  # noqa: E402
from producer import (  # type: ignore  # noqa: E402
    ProducedFlag,
    collect_flags,
    derive_gmail_query,
    discover_hook_specs,
    produce,
)
from ledger import Ledger  # type: ignore  # noqa: E402
from governor import Governor  # type: ignore  # noqa: E402

NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _write_hook(d: Path, name: str, *, label=None, query=None, status="active"):
    trig = (
        f'  gmail_label: "{label}"\n'
        if label is not None
        else f'  gmail_query: "{query}"\n'
    )
    (d / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: test hook {name}\n"
        "trigger:\n"
        f"{trig}"
        "interest_profile: |\n"
        "  things worth knowing\n"
        "scoring_context:\n"
        '  - "projects/*"\n'
        'output_wing: "vault/' + name + '"\n'
        f"status: {status}\n"
        "---\n",
        encoding="utf-8",
    )


def _fake_mempalace(record):
    mod = types.SimpleNamespace()

    def add(**kw):
        record.append(kw)
        return {"success": True, "drawer_id": "d-" + str(len(record))}

    mod.tool_add_drawer = add
    return mod


# --------------------------------------------------------------------------- #
# query derivation (generic — label OR raw query)
# --------------------------------------------------------------------------- #
def test_derive_query_from_label_quotes_slashes():
    q, label = derive_gmail_query({"trigger": {"gmail_label": "Newsletter/Dev"}})
    assert q == 'label:"Newsletter/Dev"'
    assert label == "Newsletter/Dev"


def test_derive_query_uses_raw_query_verbatim():
    q, label = derive_gmail_query(
        {"trigger": {"gmail_query": "label:foo from:bar"}}
    )
    assert q == "label:foo from:bar"
    assert label == ""


# --------------------------------------------------------------------------- #
# discovery is generic (iterates all hooks, skips invalid, not hardcoded)
# --------------------------------------------------------------------------- #
def test_discover_hook_specs_is_generic_and_resilient(tmp_path):
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    _write_hook(tmp_path, "parenting", query="label:Family/Parenting")
    (tmp_path / "broken.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    specs = discover_hook_specs(str(tmp_path / "*.md"))
    names = sorted(s["name"] for _p, s in specs)
    assert names == ["dev-libs", "parenting"]  # broken skipped, not crashed


# --------------------------------------------------------------------------- #
# collect_flags — deterministic detection
# --------------------------------------------------------------------------- #
def test_collect_flags_one_per_message_skips_paused(tmp_path):
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    _write_hook(tmp_path, "muted", label="Newsletter/Muted", status="paused")

    def search(q):
        if "Newsletter/Dev" in q:
            return [{"id": "m1", "subject": "Weekly"}, {"id": "m2", "subject": "B"}]
        return [{"id": "x9", "subject": "should-not-appear"}]

    flags = collect_flags(
        now=NOW, hooks_glob=str(tmp_path / "*.md"), gmail_search=search
    )
    assert sorted(f.message_id for f in flags) == ["m1", "m2"]
    assert all(f.hook_name == "dev-libs" for f in flags)


def test_collect_flags_dedups_same_hook_message(tmp_path):
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    flags = collect_flags(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: [{"id": "m1"}, {"id": "m1"}],
    )
    assert len(flags) == 1


def test_collect_flags_gmail_failure_is_graceful(tmp_path):
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")

    def boom(_q):
        raise RuntimeError("gmail down")

    assert collect_flags(
        now=NOW, hooks_glob=str(tmp_path / "*.md"), gmail_search=boom
    ) == []


# --------------------------------------------------------------------------- #
# canonical flag-drawer content == what triggers.parse_flag_content reads
# --------------------------------------------------------------------------- #
def test_flag_content_is_canonical_schema():
    f = ProducedFlag(
        hook_name="dev-libs",
        message_id="<abc@mail>",
        title="Newsletter/dev-libs: Weekly",
        target_date="2026-05-18",
        gmail_query='label:"Newsletter/Dev"',
        label="Newsletter/Dev",
    )
    body = f.content()
    assert body.splitlines()[0] == "NEWSLETTER-RESEARCH-QUEUE FLAG"
    assert "hook_name: dev-libs" in body
    assert "message-id: <abc@mail>" in body
    assert "target_date: 2026-05-18" in body
    assert "triggered: false" in body
    # Round-trips through the trigger's content parser.
    from triggers import parse_flag_content  # type: ignore

    parsed = parse_flag_content(body)
    assert parsed["hook_name"] == "dev-libs"
    assert parsed["message-id"] == "<abc@mail>"
    assert parsed["target_date"] == "2026-05-18"
    assert parsed["triggered"] == "false"


# --------------------------------------------------------------------------- #
# produce() — killswitch + idempotency + budget
# --------------------------------------------------------------------------- #
def test_killswitch_research_off_files_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "0")
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    rec: list = []
    out = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: [{"id": "m1"}],
        mempalace_mod=_fake_mempalace(rec),
        ledger=Ledger(tmp_path / "l.db"),
        governor=Governor(tmp_path / "l.db"),
    )
    assert out["killswitched"] is True
    assert rec == []


def test_spawn_disabled_detects_but_files_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.setenv("IGA_PROACTIVE_SPAWN", "0")
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    rec: list = []
    db = tmp_path / "l.db"
    out = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: [{"id": "m1"}, {"id": "m2"}],
        mempalace_mod=_fake_mempalace(rec),
        ledger=Ledger(db),
        governor=Governor(db),
    )
    assert out["detected"] == 2
    assert out["filed"] == 0
    assert out["spawn_disabled"] is True
    assert rec == []  # detect-but-don't-mutate: no drawer written


def test_produce_files_one_per_message_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    db = tmp_path / "l.db"
    rec: list = []
    mod = _fake_mempalace(rec)

    msgs = [{"id": "m1", "subject": "A"}, {"id": "m2", "subject": "B"}]
    out1 = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: msgs,
        mempalace_mod=mod,
        ledger=Ledger(db),
        governor=Governor(db),
    )
    assert out1["filed"] == 2
    assert len(rec) == 2
    # Each drawer body is the canonical schema, room is the queue room.
    assert all(k["room"] == "newsletter-research-queue" for k in rec)
    assert all("NEWSLETTER-RESEARCH-QUEUE FLAG" in k["content"] for k in rec)
    assert "metadata" not in rec[0]  # real MCP signature: no metadata=

    # Second tick within cooldown: producer ledger claim blocks re-filing.
    out2 = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: msgs,
        mempalace_mod=mod,
        ledger=Ledger(db),
        governor=Governor(db),
    )
    assert out2["filed"] == 0
    assert out2["claim_skipped"] == 2
    assert len(rec) == 2  # no duplicate drawers


def test_produce_per_tick_cap(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.setenv("IGA_MAX_SPAWN_PER_TICK", "1")
    _write_hook(tmp_path, "dev-libs", label="Newsletter/Dev")
    db = tmp_path / "l.db"
    rec: list = []
    out = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "*.md"),
        gmail_search=lambda q: [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
        mempalace_mod=_fake_mempalace(rec),
        ledger=Ledger(db),
        governor=Governor(db),
    )
    assert out["filed"] == 1
    assert out["capped"] == 2
    assert len(rec) == 1


def test_produce_no_hooks_files_nothing_queue_stays_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    db = tmp_path / "l.db"
    rec: list = []
    out = produce(
        now=NOW,
        hooks_glob=str(tmp_path / "none" / "*.md"),
        gmail_search=lambda q: [{"id": "m1"}],
        mempalace_mod=_fake_mempalace(rec),
        ledger=Ledger(db),
        governor=Governor(db),
    )
    # No hooks → nothing detected → queue room stays empty → consumer
    # killswitch property preserved.
    assert out["detected"] == 0
    assert out["filed"] == 0
    assert rec == []

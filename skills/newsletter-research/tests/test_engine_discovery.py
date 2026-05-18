"""newsletter-research × generic engine: discovery + safety-gate proof.

These tests assert the two contract-critical properties:

  1. The generic ``skills/iga-proactive`` engine DISCOVERS
     ``skills/newsletter-research/proactive.yaml`` as a valid job (parses,
     validates, no schema error / no false red noise).
  2. With the job in its DEFAULT state (the ``newsletter-research-queue``
     MemPalace room empty — the killswitch), a real scan tick yields NO
     worker request for it. The engine sees it; it spawns nothing.

The engine is imported read-only via its flat-import house style; nothing
here mutates the engine or the production ledger (temp db).
"""

from __future__ import annotations

import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "skills" / "iga-proactive" / "engine"
sys.path.insert(0, str(ENGINE))

import runtime as rt  # type: ignore  # noqa: E402
from schema import parse_jobs, SchemaError  # type: ignore  # noqa: E402

PROACTIVE_YAML = (
    REPO / "skills" / "newsletter-research" / "proactive.yaml"
)
SKILL_MD = REPO / "skills" / "newsletter-research" / "SKILL.md"
NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


# ---------- 1. discovery / parse ---------------------------------------


def test_proactive_yaml_exists():
    assert PROACTIVE_YAML.is_file()


def test_engine_parses_newsletter_proactive_yaml():
    text = rt._wrap_yaml_as_frontmatter(
        PROACTIVE_YAML.read_text(encoding="utf-8")
    )
    jobs = parse_jobs(text)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.id == "newsletter-research-queue"
    assert j.trigger.kind == "mempalace"
    assert "newsletter-research-queue" in j.trigger.args
    assert j.cooldown_seconds == 72 * 3600
    assert j.action.name == "spawn_worker"


def test_skill_md_proactive_scalar_no_false_error():
    """SKILL.md carries a `proactive:` scalar pointer (not a list). It must
    NOT produce a SchemaError (that would be false red noise in the board)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    # The scalar form yields zero jobs and zero errors.
    assert parse_jobs(text) == []


def test_discovery_includes_newsletter_job_no_errors(tmp_path):
    """End-to-end load against the REAL skills tree: the newsletter job is
    discovered, errors stay empty (no false red noise for the menu-bar)."""
    sources = rt.discover_job_sources(REPO / "skills")
    jobs, errors, _ = rt.load_jobs(sources)
    ids = {j.id for j in jobs}
    assert "newsletter-research-queue" in ids
    assert errors == []


# ---------- 2. safety gate: discovered but spawns nothing --------------


def _empty_mempalace():
    # Default world state: the newsletter-research-queue room is empty.
    return types.SimpleNamespace(
        tool_list_drawers=lambda **_: {"drawers": []}
    )


def test_default_state_yields_no_worker_request_for_newsletter():
    """The headline safety assertion: with the queue room empty (default,
    OFF), a real scan_tick queues NOTHING for newsletter-research."""
    with tempfile.TemporaryDirectory() as td:
        res = rt.scan_tick(
            now=NOW,
            skills_dir=REPO / "skills",
            db_path=Path(td) / "scratch.db",
            token=None,                       # no Todoist → research jobs quiet
            todoist_fetcher=lambda *_: [],
            mempalace_mod=_empty_mempalace(),
        )
    # Engine SAW the job ...
    assert res.discovered_jobs >= 1
    nl_in_errors = any("newsletter" in e for e in res.errors)
    assert not nl_in_errors
    # ... but queued NOTHING for it (empty room == killswitch).
    for qc in res.queue:
        assert qc.job.id != "newsletter-research-queue"


def test_arming_one_flag_drawer_queues_exactly_one():
    """Sanity: when the user ARMS it (one flag drawer in the queue room), the
    engine fires exactly one gated worker — the documented enable path."""
    armed = types.SimpleNamespace(
        tool_list_drawers=lambda **kw: (
            {
                "drawers": [
                    {
                        "id": "flag-1",
                        "content": "msg-id: 18ab; label Newsletter/Dev",
                        "metadata": {
                            "title": "Newsletter/Dev: weekly",
                            "target_date": "2026-05-18",
                            "triggered": "false",
                        },
                    }
                ]
            }
            if kw.get("room") == "newsletter-research-queue"
            else {"drawers": []}
        )
    )
    with tempfile.TemporaryDirectory() as td:
        res = rt.scan_tick(
            now=NOW,
            skills_dir=REPO / "skills",
            db_path=Path(td) / "scratch.db",
            token=None,
            todoist_fetcher=lambda *_: [],
            mempalace_mod=armed,
        )
    nl = [qc for qc in res.queue if qc.job.id == "newsletter-research-queue"]
    assert len(nl) == 1
    assert nl[0].idempotency_key == "newsletter::flag-1::2026-05-18"

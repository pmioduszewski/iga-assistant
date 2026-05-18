"""habit-tracker × generic proactive engine: discovery + safety-gate proof.

Mirrors skills/newsletter-research/tests/test_engine_discovery.py. Asserts:
  1. The generic skills/iga-proactive engine DISCOVERS
     skills/habit-tracker/proactive.yaml as a valid job (parses, validates,
     no SchemaError / no false red board noise).
  2. In its DEFAULT state (the habit-reflection-queue MemPalace room empty —
     the killswitch) a real scan tick yields NO worker request for it. The
     engine sees it; it spawns nothing.
  3. The SKILL.md `proactive:` scalar pointer yields zero jobs / zero errors.
  4. Arming one flag drawer queues exactly one gated worker.

Engine imported read-only; nothing here mutates the engine or the
production ledger (temp db).
"""

from __future__ import annotations

import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "skills" / "iga-proactive" / "engine"
sys.path.insert(0, str(ENGINE))

import runtime as rt  # type: ignore  # noqa: E402
from schema import parse_jobs  # type: ignore  # noqa: E402

PROACTIVE_YAML = REPO / "skills" / "habit-tracker" / "proactive.yaml"
SKILL_MD = REPO / "skills" / "habit-tracker" / "SKILL.md"
NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def test_proactive_yaml_exists():
    assert PROACTIVE_YAML.is_file()


def test_engine_parses_habit_proactive_yaml():
    text = rt._wrap_yaml_as_frontmatter(
        PROACTIVE_YAML.read_text(encoding="utf-8")
    )
    jobs = parse_jobs(text)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.id == "habit-reflection-queue"
    assert j.trigger.kind == "mempalace"
    assert "habit-reflection-queue" in j.trigger.args
    assert j.cooldown_seconds == 72 * 3600
    assert j.action.name == "spawn_worker"
    assert "reflection.prompt.md" in j.action.args


def test_skill_md_proactive_scalar_no_false_error():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert parse_jobs(text) == []


def test_discovery_includes_habit_job_no_errors():
    sources = rt.discover_job_sources(REPO / "skills")
    jobs, errors, _ = rt.load_jobs(sources)
    assert "habit-reflection-queue" in {j.id for j in jobs}
    assert errors == []


def _empty_mempalace():
    return types.SimpleNamespace(
        tool_list_drawers=lambda **_: {"drawers": []}
    )


def test_default_state_yields_no_worker_request_for_habits():
    with tempfile.TemporaryDirectory() as td:
        res = rt.scan_tick(
            now=NOW,
            skills_dir=REPO / "skills",
            db_path=Path(td) / "scratch.db",
            token=None,
            todoist_fetcher=lambda *_: [],
            mempalace_mod=_empty_mempalace(),
        )
    assert res.discovered_jobs >= 1
    assert not any("habit" in e for e in res.errors)
    for qc in res.queue:
        assert qc.job.id != "habit-reflection-queue"


def test_arming_one_flag_drawer_queues_exactly_one():
    armed = types.SimpleNamespace(
        tool_list_drawers=lambda **kw: (
            {
                "drawers": [
                    {
                        "id": "hflag-1",
                        "content": "weekly habit reflection",
                        "metadata": {
                            "title": "Habit reflection: week",
                            "target_date": "2026-05-18",
                            "triggered": "false",
                        },
                    }
                ]
            }
            if kw.get("room") == "habit-reflection-queue"
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
    hb = [qc for qc in res.queue if qc.job.id == "habit-reflection-queue"]
    assert len(hb) == 1
    assert hb[0].idempotency_key == "habit-reflection::hflag-1::2026-05-18"

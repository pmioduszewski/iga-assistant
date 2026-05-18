"""Schema tests — duration parsing, job validation, frontmatter extraction."""

import sys
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from schema import (  # noqa: E402
    parse_jobs,
    validate,
    parse_duration_to_seconds,
    SchemaError,
    Trigger,
    Action,
)


# --------------------------- duration parsing ---------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("48h", 48 * 3600),
        ("7d", 7 * 24 * 3600),
        ("1h30m", 3600 + 1800),
        ("2w", 2 * 7 * 24 * 3600),
        ("90m", 5400),
        ("45s", 45),
        ("3600", 3600),
        (3600, 3600),
        ("1d12h", 24 * 3600 + 12 * 3600),
    ],
)
def test_parse_duration_ok(raw, expected):
    assert parse_duration_to_seconds(raw) == expected


@pytest.mark.parametrize("bad", ["", "abc", "0h", "0", -5, "h", "1x", True])
def test_parse_duration_bad(bad):
    with pytest.raises(SchemaError):
        parse_duration_to_seconds(bad)


# --------------------------- trigger/action ------------------------------ #
def test_trigger_parse_known_kinds():
    t = Trigger.parse("todoist(label:iga-research, due:<7d)")
    assert t.kind == "todoist"
    assert t.args == "label:iga-research, due:<7d"
    assert t.raw == "todoist(label:iga-research, due:<7d)"


def test_trigger_manual_no_parens():
    t = Trigger.parse("manual")
    assert t.kind == "manual"
    assert t.args == ""


def test_trigger_unknown_kind_raises():
    with pytest.raises(SchemaError):
        Trigger.parse("frobnicate(x)")


def test_action_parse():
    a = Action.parse("spawn_worker(prompt: x.md, depth: deep)")
    assert a.name == "spawn_worker"
    assert a.args == "prompt: x.md, depth: deep"


# --------------------------- job validation ------------------------------ #
def _valid_raw(**over):
    base = {
        "id": "job1",
        "trigger": "todoist(label:iga-research)",
        "action": "spawn_worker(prompt: p.md)",
        "idempotency_key": "research::{{task.id}}",
        "cooldown": "48h",
    }
    base.update(over)
    return base


def test_validate_ok():
    validate(_valid_raw())  # no raise


@pytest.mark.parametrize("missing", ["id", "trigger", "action", "idempotency_key", "cooldown"])
def test_validate_missing_required(missing):
    raw = _valid_raw()
    del raw[missing]
    with pytest.raises(SchemaError):
        validate(raw)


def test_validate_bad_deliver():
    with pytest.raises(SchemaError):
        validate(_valid_raw(deliver="carrier_pigeon"))


def test_validate_default_deliver_applied():
    jobs = parse_jobs({"proactive": [_valid_raw()]})
    assert jobs[0].deliver == "surface_next_brief"


def test_parse_jobs_keeps_idempotency_template_verbatim():
    jobs = parse_jobs({"proactive": [_valid_raw(idempotency_key="r::{{x.y}}::{{z}}")]})
    assert jobs[0].idempotency_key == "r::{{x.y}}::{{z}}"


def test_parse_jobs_duplicate_id_raises():
    with pytest.raises(SchemaError):
        parse_jobs({"proactive": [_valid_raw(id="dup"), _valid_raw(id="dup")]})


def test_parse_jobs_cooldown_seconds_computed():
    jobs = parse_jobs({"proactive": [_valid_raw(cooldown="48h")]})
    assert jobs[0].cooldown_seconds == 48 * 3600
    assert jobs[0].cooldown == "48h"


def test_parse_jobs_empty_when_no_block():
    assert parse_jobs({}) == []


# --------------------------- frontmatter text ---------------------------- #
SKILL_MD = """---
name: demo-skill
description: a demo
status: building
proactive:
  - id: prep-research
    trigger: todoist(label:iga-research, due:<7d)
    condition: not exists drawer for task
    action: spawn_worker(prompt: research.md, depth: deep)
    idempotency_key: research::{{task.id}}::{{task.due}}
    budget:
      model: opus
      wall_min: 20
    deliver: surface_next_brief
    cooldown: 48h
  - id: cal-prep
    trigger: calendar(window:48h)
    action: spawn_worker(prompt: meeting_prep.md)
    idempotency_key: calprep::{{event.id}}
    cooldown: 24h
triggers:
  - kind: hook
    spec: something
---

# Demo Skill body here
"""


def test_parse_jobs_from_skill_md_text():
    jobs = parse_jobs(SKILL_MD)
    assert len(jobs) == 2

    j0 = jobs[0]
    assert j0.id == "prep-research"
    assert j0.trigger.kind == "todoist"
    assert j0.trigger.args == "label:iga-research, due:<7d"
    assert j0.condition == "not exists drawer for task"
    assert j0.action.name == "spawn_worker"
    assert j0.idempotency_key == "research::{{task.id}}::{{task.due}}"
    assert j0.budget == {"model": "opus", "wall_min": 20}
    assert j0.deliver == "surface_next_brief"
    assert j0.cooldown_seconds == 48 * 3600

    j1 = jobs[1]
    assert j1.id == "cal-prep"
    assert j1.trigger.kind == "calendar"
    assert j1.deliver == "surface_next_brief"  # default applied
    assert j1.cooldown_seconds == 24 * 3600


def test_parse_jobs_no_proactive_block_returns_empty():
    txt = "---\nname: x\nstatus: building\n---\n# body\n"
    assert parse_jobs(txt) == []


def test_parse_jobs_no_frontmatter_raises():
    with pytest.raises(SchemaError):
        parse_jobs("just text, no fence")

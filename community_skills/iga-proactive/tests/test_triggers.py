"""Trigger-evaluator tests — fully mocked I/O (no network, no MCP).

Mirrors skills/iga-proactive-research/tests/test_scanner.py mocking style:
a types.SimpleNamespace fake for MemPalace, an injected fetcher for Todoist.
"""

import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from schema import Trigger  # noqa: E402
import triggers as trig  # noqa: E402
from triggers import (  # noqa: E402
    Candidate,
    cron_matches,
    evaluate,
    parse_kv_args,
)

NOW = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)  # a Saturday


# ---------- arg parsing -------------------------------------------------
def test_parse_kv_args_basic():
    assert parse_kv_args("label:iga-research, due:<7d") == {
        "label": "iga-research",
        "due": "<7d",
    }


def test_parse_kv_args_keeps_value_after_first_colon():
    assert parse_kv_args("window:48h")["window"] == "48h"


def test_parse_kv_args_bare_token():
    assert parse_kv_args("research-queue") == {"research-queue": ""}


def test_parse_kv_args_empty():
    assert parse_kv_args("") == {}


# ---------- cron --------------------------------------------------------
def test_cron_wildcard_matches_any_minute():
    assert cron_matches("* * * * *", NOW) is True


def test_cron_specific_minute_hour():
    assert cron_matches("0 9 * * *", NOW) is True
    assert cron_matches("30 9 * * *", NOW) is False
    assert cron_matches("0 10 * * *", NOW) is False


def test_cron_step_and_range():
    assert cron_matches("*/15 * * * *", NOW) is True  # minute 0
    at_07 = datetime(2026, 5, 16, 7, 5, tzinfo=timezone.utc)
    assert cron_matches("5 7-19 * * *", at_07) is True
    assert cron_matches("5 8-19 * * *", at_07) is False


def test_cron_dow_sunday_zero_or_seven():
    sunday = datetime(2026, 5, 17, 9, 0, tzinfo=timezone.utc)  # Sun
    assert cron_matches("0 9 * * 0", sunday) is True
    assert cron_matches("0 9 * * 7", sunday) is True
    assert cron_matches("0 9 * * 1", sunday) is False  # not Monday


def test_cron_invalid_field_count_raises():
    with pytest.raises(ValueError):
        cron_matches("* * * *", NOW)


# ---------- schedule trigger -------------------------------------------
def test_eval_schedule_fires_on_match():
    t = Trigger.parse("schedule(0 9 * * *)")
    out = evaluate(t, now=NOW)
    assert len(out) == 1
    assert out[0].trigger_kind == "schedule"
    assert out[0].source_id == "2026-05-16T09:00"
    assert out[0].context["schedule.tick"] == "2026-05-16T09:00"


def test_eval_schedule_no_fire_off_match():
    t = Trigger.parse("schedule(30 9 * * *)")
    assert evaluate(t, now=NOW) == []


def test_eval_schedule_invalid_cron_graceful_empty():
    t = Trigger.parse("schedule(not a cron)")
    assert evaluate(t, now=NOW) == []


# ---------- todoist trigger --------------------------------------------
def _todoist_tasks():
    return [
        {
            "id": 101,
            "content": "Prep Acme demo",
            "description": "Big pitch",
            "due": {"date": "2026-05-18"},
        },
        {
            "id": 102,
            "content": "Far future task",
            "due": {"date": "2026-09-01"},
        },
        {
            "id": 103,
            "content": "Overdue task",
            "due": {"date": "2026-05-10"},
        },
        {"id": 104, "content": "No due date task"},
    ]


def test_eval_todoist_filters_by_due_window():
    t = Trigger.parse("todoist(label:iga-research, due:<7d)")
    out = evaluate(
        t,
        now=NOW,
        token="fake",
        todoist_fetcher=lambda tok, label: _todoist_tasks(),
    )
    # Only task 101 is within [today, +7d] and not overdue.
    assert len(out) == 1
    c = out[0]
    assert c.trigger_kind == "todoist"
    assert c.source_id == "101"
    assert c.context["task.id"] == "101"
    assert c.context["task.due"] == "2026-05-18"


def test_eval_todoist_no_token_graceful_empty(monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    monkeypatch.setattr(trig, "_TODOIST_TOKEN_FILE", "/nonexistent/token/path")
    t = Trigger.parse("todoist(label:iga-research, due:<7d)")
    # No token, no fetcher -> nothing, no raise.
    assert evaluate(t, now=NOW) == []


def test_eval_todoist_fetcher_exception_graceful_empty():
    def boom(tok, label):
        raise RuntimeError("api down")

    t = Trigger.parse("todoist(label:iga-research, due:<7d)")
    assert evaluate(t, now=NOW, token="fake", todoist_fetcher=boom) == []


def test_eval_todoist_no_due_filter_returns_all_titled():
    t = Trigger.parse("todoist(label:iga-research)")
    out = evaluate(
        t, now=NOW, token="fake", todoist_fetcher=lambda *_: _todoist_tasks()
    )
    # No due:<Nd -> all tasks with a non-empty title (4 of them).
    assert len(out) == 4


# ---------- mempalace trigger ------------------------------------------
def _fake_mempalace(drawers):
    mod = types.SimpleNamespace()
    mod.tool_list_drawers = lambda **_: {"drawers": list(drawers)}
    return mod


def test_eval_mempalace_skips_triggered_drawers():
    drawers = [
        {
            "id": "d1",
            "content": "Research X before launch",
            "metadata": {
                "title": "Research X",
                "target_date": "2026-05-20",
                "triggered": "false",
            },
        },
        {
            "id": "d2",
            "content": "Already consumed",
            "metadata": {"title": "Done", "triggered": "true"},
        },
    ]
    t = Trigger.parse("mempalace(room:research-queue)")
    out = evaluate(t, now=NOW, mempalace_mod=_fake_mempalace(drawers))
    assert len(out) == 1
    assert out[0].trigger_kind == "mempalace"
    assert out[0].source_id == "d1"
    assert out[0].context["drawer.target_date"] == "2026-05-20"


def test_eval_mempalace_unavailable_graceful_empty():
    t = Trigger.parse("mempalace(room:research-queue)")

    class Boom:
        def tool_list_drawers(self, **_):
            raise RuntimeError("mcp gone")

    assert evaluate(t, now=NOW, mempalace_mod=Boom()) == []


def test_eval_mempalace_error_dict_graceful_empty():
    mod = types.SimpleNamespace()
    mod.tool_list_drawers = lambda **_: {"error": "boom"}
    t = Trigger.parse("mempalace(room:research-queue)")
    assert evaluate(t, now=NOW, mempalace_mod=mod) == []


# ---------- manual trigger ---------------------------------------------
def test_eval_manual_always_one_candidate():
    t = Trigger.parse("manual")
    out = evaluate(t, now=NOW)
    assert len(out) == 1
    assert out[0].trigger_kind == "manual"
    assert out[0].source_id == "manual"


# ---------- calendar / watch are Wave 3 stubs --------------------------
def test_calendar_trigger_is_wave3_stub():
    t = Trigger.parse("calendar(window:48h)")
    with pytest.raises(NotImplementedError):
        evaluate(t, now=NOW)


def test_watch_trigger_is_wave3_stub():
    t = Trigger.parse("watch(file_changed)")
    with pytest.raises(NotImplementedError):
        evaluate(t, now=NOW)


# ---------- render_context ---------------------------------------------
def test_candidate_render_context_includes_defaults():
    c = Candidate("todoist", "T9", "Title", {"task.id": "T9"})
    ns = c.render_context()
    assert ns["trigger.kind"] == "todoist"
    assert ns["source.id"] == "T9"
    assert ns["candidate.title"] == "Title"
    assert ns["task.id"] == "T9"

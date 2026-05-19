"""Dispatcher tests — WORKER_REQUEST shape + JSON state file contract."""

import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from governor import Governor  # noqa: E402
from runtime import scan_tick  # noqa: E402
import dispatcher as disp  # noqa: E402
from dispatcher import (  # noqa: E402
    extract_prompt_path,
    build_dispatch,
    read_state,
    STATE_SCHEMA_VERSION,
)

NOW = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)

_JOB = textwrap.dedent(
    """\
    proactive:
      - id: research-todoist
        trigger: todoist(label:iga-research, due:<7d)
        condition: not exists drawer for task
        action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)
        idempotency_key: research::{{task.id}}::{{task.due}}
        budget:
          model: claude-opus-4-7[1m]
          wall_min: 30
        deliver: surface_next_brief
        cooldown: 48h
    """
)


def _make_skill(sd, name, block):
    d = sd / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: t\n{block}---\n\n# B\n",
        encoding="utf-8",
    )
    return d


def _one_task():
    return [
        {
            "id": "101",
            "content": "Prep Acme demo",
            "description": "ctx",
            "due": {"date": "2026-05-18"},
        }
    ]


# ---------- extract_prompt_path ----------------------------------------
def test_extract_prompt_path_relative_resolves_against_base(tmp_path):
    base = tmp_path / "skills" / "iga-proactive-research"
    base.mkdir(parents=True)
    (base / "engine").mkdir()
    p = extract_prompt_path("prompt: engine/worker.prompt.md, depth: deep", base=base)
    assert p == str((base / "engine" / "worker.prompt.md").resolve())


def test_extract_prompt_path_none_when_absent():
    assert extract_prompt_path("depth: deep") is None
    assert extract_prompt_path("") is None


# ---------- WORKER_REQUEST shape ---------------------------------------
def test_build_dispatch_request_shape(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _JOB)
    db = tmp_path / "proactive.db"
    res = scan_tick(
        now=NOW, skills_dir=sd, db_path=db, token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    reqs, state = build_dispatch(
        res, prompt_base=sd / "research", write_state=False
    )
    assert len(reqs) == 1
    r = reqs[0]
    assert set(r.keys()) >= {
        "job_id",
        "idempotency_key",
        "trigger_kind",
        "action",
        "action_name",
        "prompt_path",
        "model",
        "est_tokens",
        "deliver",
        "context",
    }
    assert r["job_id"] == "research-todoist"
    assert r["idempotency_key"] == "research::101::2026-05-18"
    assert r["action_name"] == "spawn_worker"
    assert r["prompt_path"].endswith("worker.prompt.md")
    assert r["model"] == "claude-opus-4-7[1m]"
    assert r["est_tokens"] == 30 * 10_000  # wall_min 30 -> derived
    assert r["deliver"] == "surface_next_brief"
    assert r["context"]["task.id"] == "101"


# ---------- JSON state file --------------------------------------------
def test_state_file_written_and_schema(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _JOB)
    db = tmp_path / "proactive.db"
    state_path = tmp_path / "state" / "proactive-state.json"
    gov = Governor(db)

    res = scan_tick(
        now=NOW, skills_dir=sd, db_path=db, governor=gov, token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    reqs, state = build_dispatch(
        res, governor=gov, prompt_base=sd / "research", state_path=state_path
    )
    assert state_path.is_file()
    on_disk = json.loads(state_path.read_text())
    assert on_disk["schema_version"] == STATE_SCHEMA_VERSION
    assert on_disk["counts"]["queued"] == 1
    assert on_disk["counts"]["running"] == 0
    assert on_disk["counts"]["done"] == 0
    assert "generated_at" in on_disk
    assert on_disk["tick"]["discovered_jobs"] == 1
    assert on_disk["tick"]["fired_candidates"] == 1
    assert len(on_disk["queue"]) == 1
    assert "invocations_5h" in on_disk["governor"]
    # read_state round-trips.
    assert read_state(state_path) == on_disk


def test_state_file_atomic_no_tmp_left(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _JOB)
    state_path = tmp_path / "s.json"
    res = scan_tick(
        now=NOW, skills_dir=sd, db_path=tmp_path / "db", token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    build_dispatch(res, prompt_base=sd / "research", state_path=state_path)
    # No leftover .tmp sidecar.
    assert not (tmp_path / "s.json.tmp").exists()
    assert state_path.is_file()


def test_default_state_path_uses_scratch(monkeypatch):
    monkeypatch.delenv("IGA_PROACTIVE_STATE", raising=False)
    p = disp.default_state_path()
    # scratch/ is gitignored -> git status stays clean by construction.
    assert "scratch" in str(p)
    assert p.name == "proactive-state.json"


def test_state_path_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom-state.json"
    monkeypatch.setenv("IGA_PROACTIVE_STATE", str(target))
    assert disp.default_state_path() == target


def test_read_state_missing_returns_empty(tmp_path):
    assert read_state(tmp_path / "nope.json") == {}


# --------------------------------------------------------------------------- #
# drain_cancellations — UI cancel-file → ledger sticky cancel
# --------------------------------------------------------------------------- #
def test_drain_cancellations_marks_keys_and_clears_file(tmp_path):
    from ledger import Ledger

    led = Ledger(tmp_path / "p.db")
    led.claim("k-keep", "job", cooldown_seconds=3600)
    led.claim("k-cancel", "job", cooldown_seconds=3600)

    cf = tmp_path / "cancel.json"
    cf.write_text(json.dumps({"cancel": ["k-cancel"]}), encoding="utf-8")

    processed = disp.drain_cancellations(led, cf)

    assert processed == ["k-cancel"]
    assert led.should_skip("k-cancel") is True
    assert led.claim("k-cancel", "job", cooldown_seconds=0) is False
    # untouched key still behaves normally
    assert led.should_skip("k-keep") is True  # still in cooldown
    # file drained to empty
    assert json.loads(cf.read_text())["cancel"] == []


def test_drain_cancellations_missing_or_empty_file_is_noop(tmp_path):
    from ledger import Ledger

    led = Ledger(tmp_path / "p.db")
    assert disp.drain_cancellations(led, tmp_path / "nope.json") == []
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"cancel": []}), encoding="utf-8")
    assert disp.drain_cancellations(led, empty) == []


def test_drain_preserves_keys_appended_during_drain(tmp_path):
    """If the file gains a new key between snapshot and rewrite, the late
    append must survive (not be silently dropped)."""
    from ledger import Ledger

    led = Ledger(tmp_path / "p.db")
    cf = tmp_path / "cancel.json"
    cf.write_text(json.dumps({"cancel": ["a"]}), encoding="utf-8")

    real_cancel = led.cancel

    def cancel_then_append(key, *args, **kwargs):
        real_cancel(key, *args, **kwargs)
        # Simulate the app appending a new cancel mid-drain.
        cf.write_text(json.dumps({"cancel": ["a", "b"]}), encoding="utf-8")

    led.cancel = cancel_then_append  # type: ignore[method-assign]
    processed = disp.drain_cancellations(led, cf)

    assert processed == ["a"]
    assert json.loads(cf.read_text())["cancel"] == ["b"]  # late append kept


# ---------- per-job prompt resolution (prompt_base=None) ----------------
def test_prompt_base_none_resolves_each_job_against_its_own_skill_dir(tmp_path):
    """Two skills with the same relative prompt: must resolve to DIFFERENT
    absolute paths — each against its own source dir — when build_dispatch
    is called WITHOUT an explicit prompt_base (the production path)."""
    sd = tmp_path / "skills"
    block_a = (
        "proactive:\n"
        "  - id: job-a\n"
        "    trigger: todoist(label:iga-research, due:<7d)\n"
        "    action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)\n"
        "    idempotency_key: a::{{task.id}}\n"
        "    cooldown: 48h\n"
    )
    block_b = (
        "proactive:\n"
        "  - id: job-b\n"
        "    trigger: todoist(label:iga-research, due:<7d)\n"
        "    action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)\n"
        "    idempotency_key: b::{{task.id}}\n"
        "    cooldown: 48h\n"
    )
    _make_skill(sd, "skill-a", block_a)
    _make_skill(sd, "skill-b", block_b)

    res = scan_tick(
        now=NOW, skills_dir=sd, db_path=tmp_path / "p.db", token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    # The production call site (cli._run_live → build_dispatch) passes NO
    # prompt_base — exactly the path the prompt-resolution fix targets.
    reqs, _ = build_dispatch(res, prompt_base=None, write_state=False)

    by_job = {r["job_id"]: r["prompt_path"] for r in reqs}
    assert set(by_job) == {"job-a", "job-b"}, by_job
    assert by_job["job-a"] == str(
        (sd / "skill-a" / "engine" / "worker.prompt.md").resolve())
    assert by_job["job-b"] == str(
        (sd / "skill-b" / "engine" / "worker.prompt.md").resolve())
    assert by_job["job-a"] != by_job["job-b"]


def test_drain_corrupt_file_warns_preserves_and_noops(tmp_path):
    """A cancel file that exists but won't parse must NOT be a silent
    no-op: it is preserved as .corrupt and the tick continues."""
    from ledger import Ledger

    led = Ledger(tmp_path / "p.db")
    cf = tmp_path / "cancel.json"
    cf.write_text("{ this is not json", encoding="utf-8")
    assert disp.drain_cancellations(led, cf) == []
    # original clobbered file moved aside for debugging
    assert (tmp_path / "cancel.json.corrupt").exists()

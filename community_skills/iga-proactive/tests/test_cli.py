"""CLI relay tests — the entrypoint must RELAY the frozen engine, not bypass it.

These exercise the CLI in-process (``cli.main([...])``) with mocked trigger
I/O (a fake Todoist fetcher monkeypatched onto ``triggers``), proving three
things end to end *through the CLI path*:

  1. ``scan`` emits the correct WORKER_REQUEST JSON on stdout AND writes the
     frozen JSON state file.
  2. ``scan --dry-run`` mutates NOTHING — ledger row count unchanged, no
     state file created.
  3. A second non-dry ``scan`` of the SAME candidate inside its cooldown
     window yields an EMPTY queue (the anti-duplicate guarantee survives the
     CLI path — dedup is the frozen ledger's, relayed faithfully).

House style mirrors ``test_runtime.py``: ``engine/`` on ``sys.path`` (flat
import), a temp skills dir, temp db/state via env vars.
"""

import json
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

import cli  # noqa: E402
import triggers as triggers_mod  # noqa: E402
import runtime as runtime_mod  # noqa: E402
from ledger import Ledger  # noqa: E402

NOW = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)

_TODOIST_JOB = textwrap.dedent(
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


def _make_skill(skills_dir: Path, name: str, block: str) -> Path:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: test skill\n"
        f"{block}"
        "---\n\n# Body\n",
        encoding="utf-8",
    )
    return d


def _one_task(tid="101", due="2026-05-18"):
    return [
        {
            "id": tid,
            "content": "Prep Acme demo",
            "description": "ctx",
            "due": {"date": due},
        }
    ]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A temp skills dir with one Todoist job, a temp db/state via env, a
    fake Todoist fetcher + a fixed 'now', and the skills dir pinned so the
    CLI's scan_tick discovers exactly our test job.

    We patch ``triggers._default_todoist_fetch`` (the real network entry point) and
    a fixed token so the CLI runs the *real* engine with mocked I/O only —
    no monkeypatching of the engine's admission logic whatsoever.
    """
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    db = tmp_path / "proactive.db"
    state = tmp_path / "state" / "proactive-state.json"

    monkeypatch.setenv("IGA_PROACTIVE_DB", str(db))
    monkeypatch.setenv("IGA_PROACTIVE_STATE", str(state))
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake-token")

    # Mock the network entry point only.
    monkeypatch.setattr(
        triggers_mod, "_default_todoist_fetch", lambda token, label: _one_task()
    )
    # Pin discovery to our temp skills dir + a fixed clock so the test is
    # deterministic. scan_tick reads _SKILLS_DIR_DEFAULT when skills_dir is
    # None (the CLI passes None — it never overrides discovery).
    monkeypatch.setattr(runtime_mod, "_SKILLS_DIR_DEFAULT", sd)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(cli, "datetime", _FixedDatetime)
    return {"db": db, "state": state, "sd": sd}


def _ledger_row_count(db: Path) -> int:
    if not db.is_file():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM job_runs;").fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 1. scan emits correct WORKER_REQUEST JSON + writes state
# --------------------------------------------------------------------------- #
def test_scan_emits_worker_request_json_and_writes_state(wired, capsys):
    rc = cli.main(["scan"])
    assert rc == 0

    out = capsys.readouterr().out
    reqs = json.loads(out)
    assert isinstance(reqs, list) and len(reqs) == 1
    req = reqs[0]
    assert req["job_id"] == "research-todoist"
    assert req["idempotency_key"] == "research::101::2026-05-18"
    assert req["action_name"] == "spawn_worker"
    assert req["model"] == "claude-opus-4-7[1m]"
    assert req["context"]["task.id"] == "101"

    # State file written, v1 schema, queue echoed.
    assert wired["state"].is_file()
    state = json.loads(wired["state"].read_text())
    assert state["schema_version"] == 1
    assert state["counts"]["queued"] == 1
    assert state["queue"][0]["idempotency_key"] == "research::101::2026-05-18"

    # The real ledger now holds the claimed row (the CLI relayed a real tick).
    assert _ledger_row_count(wired["db"]) == 1


def test_scan_json_flag_is_machine_readable(wired, capsys):
    rc = cli.main(["scan", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is False
    assert payload["tick"]["discovered_jobs"] == 1
    assert payload["tick"]["fired_candidates"] == 1
    assert len(payload["queue"]) == 1
    assert payload["state_path"] == str(wired["state"])
    assert payload["counts"]["queued"] == 1


# --------------------------------------------------------------------------- #
# 2. --dry-run mutates NOTHING
# --------------------------------------------------------------------------- #
def test_dry_run_mutates_nothing(wired, capsys):
    assert _ledger_row_count(wired["db"]) == 0
    assert not wired["state"].is_file()

    rc = cli.main(["scan", "--dry-run"])
    assert rc == 0

    out = capsys.readouterr().out
    would = json.loads(out)
    assert len(would) == 1
    assert would[0]["idempotency_key"] == "research::101::2026-05-18"

    # The whole point: production ledger untouched, no state file.
    assert _ledger_row_count(wired["db"]) == 0
    assert not wired["state"].is_file()


def test_spawn_killswitch_env_behaves_like_dry_run(wired, capsys, monkeypatch):
    monkeypatch.setenv("IGA_PROACTIVE_SPAWN", "0")
    rc = cli.main(["scan"])
    assert rc == 0
    json.loads(capsys.readouterr().out)  # still valid JSON
    assert _ledger_row_count(wired["db"]) == 0
    assert not wired["state"].is_file()


def test_research_killswitch_disables_engine(wired, capsys, monkeypatch):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "0")
    rc = cli.main(["scan"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []
    assert _ledger_row_count(wired["db"]) == 0
    assert not wired["state"].is_file()


# --------------------------------------------------------------------------- #
# 3. second non-dry scan of same candidate within cooldown → empty (dedup
#    survives end to end through the CLI path)
# --------------------------------------------------------------------------- #
def test_second_scan_within_cooldown_is_empty_through_cli(wired, capsys):
    rc1 = cli.main(["scan"])
    assert rc1 == 0
    reqs1 = json.loads(capsys.readouterr().out)
    assert len(reqs1) == 1
    assert _ledger_row_count(wired["db"]) == 1

    # Same task, same fixed clock — well inside the 48h cooldown.
    rc2 = cli.main(["scan"])
    assert rc2 == 0
    reqs2 = json.loads(capsys.readouterr().out)
    assert reqs2 == [], (
        f"anti-duplicate violated through CLI: {len(reqs2)} requests on "
        f"the 2nd scan of one task inside its cooldown (expected 0)"
    )
    # Ledger still exactly one row — no duplicate claim.
    assert _ledger_row_count(wired["db"]) == 1

    # And dry-run after a real claim also reports it would be skipped.
    rc3 = cli.main(["scan", "--dry-run", "--json"])
    assert rc3 == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["would_queue"] == []
    assert len(payload["would_skip"]) == 1
    assert payload["would_skip"][0]["idempotency_key"] == "research::101::2026-05-18"


# --------------------------------------------------------------------------- #
# help / no-command
# --------------------------------------------------------------------------- #
def test_no_command_prints_help_exit_0(capsys):
    rc = cli.main([])
    assert rc == 0
    assert "scan" in capsys.readouterr().out

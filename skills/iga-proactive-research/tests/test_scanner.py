"""Tests for the Iga Proactive Research scanner (v2)."""

from __future__ import annotations

import io
import json
import types
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import scanner as scanner  # type: ignore
from scanner import (  # type: ignore
    Candidate,
    fetch_mempalace_flag_candidates,
    is_duplicate,
    normalize_title,
    read_queue,
    run,
    topic_hash,
    write_queue,
)


# ---------- topic_hash --------------------------------------------------


def test_topic_hash_deterministic():
    a = topic_hash("Demo with Acme", "2026-05-20")
    b = topic_hash("Demo with Acme", "2026-05-20")
    assert a == b
    assert len(a) == 16


def test_topic_hash_ignores_case_whitespace_emoji_punct():
    h1 = topic_hash("Demo with Acme", "2026-05-20")
    h2 = topic_hash("  demo   with   acme!  ", "2026-05-20")
    h3 = topic_hash("🚀 Demo with Acme!!!", "2026-05-20")
    h4 = topic_hash("Demo, with: Acme.", "2026-05-20")
    assert h1 == h2 == h3 == h4


def test_topic_hash_ignores_date():
    # target_date is deliberately NOT part of the identity: rescheduling a
    # Todoist task must not mint a new hash and re-trigger research that's
    # already filed. (Regression guard for the "researched 3x in 2 weeks" bug.)
    assert topic_hash("X", "2026-05-20") == topic_hash("X", "2026-05-21")


def test_normalize_title_preserves_diacritics():
    # Diacritics are preserved on purpose — "café" ≠ "cafe".
    assert normalize_title("Café RÉSUMÉ!") == "café résumé"


# ---------- dedup -------------------------------------------------------


def _fake_mempalace(drawers, search_results=None):
    mod = types.SimpleNamespace()
    mod.tool_list_drawers = lambda **_: {"drawers": list(drawers)}
    mod.tool_search = lambda **_: {"results": list(search_results or [])}
    mod.tool_add_drawer = mock.Mock(return_value={"id": "new"})
    mod.tool_update_drawer = mock.Mock(return_value={"ok": True})
    return mod


def test_is_duplicate_returns_true_for_fresh_drawer():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    h = topic_hash("Foo", "2026-05-20")
    drawers = [
        {
            "id": "d1",
            "content": f"RESEARCH:{h}|2026-05-20|depth:shallow|★★★\nTLDR: ...",
            "metadata": {"last_updated": (now - timedelta(hours=10)).isoformat()},
        }
    ]
    mod = _fake_mempalace(drawers)
    cand = Candidate(h, "todoist", "T1", "Foo", "", "2026-05-20", "shallow")
    assert is_duplicate(mod, cand, now=now) is True


def test_is_duplicate_dedupes_drawer_days_old():
    # A 3-day-old research answer is NOT stale — it must still dedupe. The old
    # 48-hour window re-ran anything older than 2 days, which is the core bug.
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    h = topic_hash("Foo", "2026-05-20")
    drawers = [
        {
            "id": "d1",
            "content": f"RESEARCH:{h}|2026-05-20|depth:shallow|★★★",
            "metadata": {"last_updated": (now - timedelta(hours=72)).isoformat()},
        }
    ]
    mod = _fake_mempalace(drawers)
    cand = Candidate(h, "todoist", "T1", "Foo", "", "2026-05-20", "shallow")
    assert is_duplicate(mod, cand, now=now) is True


def test_is_duplicate_allows_refresh_past_staleness_horizon():
    # Genuinely old research (>90 days) may be refreshed → not a duplicate.
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    h = topic_hash("Foo", "2026-05-20")
    drawers = [
        {
            "id": "d1",
            "content": f"RESEARCH:{h}|2026-05-20|depth:shallow|★★★",
            "metadata": {"last_updated": (now - timedelta(days=100)).isoformat()},
        }
    ]
    mod = _fake_mempalace(drawers)  # tool_search returns no hits
    cand = Candidate(h, "todoist", "T1", "Foo", "", "2026-05-20", "shallow")
    assert is_duplicate(mod, cand, now=now) is False


def test_is_duplicate_semantic_fallback_catches_defunct_hash():
    # The same topic filed under an OLD hash (target_date used to be in the
    # hash) has a different topic_hash, so the prefix path misses it — the
    # semantic fallback must still catch it and skip the re-research.
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    drawers = [
        {
            "id": "d_old",
            "content": "RESEARCH:deadbeefdeadbeef|2026-04-01|depth:shallow|★★★",
            "metadata": {"last_updated": (now - timedelta(days=5)).isoformat()},
        }
    ]
    search_results = [
        {
            "drawer_id": "d_old",
            "distance": 0.12,
            "metadata": {"last_updated": (now - timedelta(days=5)).isoformat()},
        }
    ]
    mod = _fake_mempalace(drawers, search_results=search_results)
    cand = Candidate(
        topic_hash("Foo", "2026-05-20"), "todoist", "T1", "Foo", "", "2026-05-20", "shallow"
    )
    assert is_duplicate(mod, cand, now=now) is True


def test_is_duplicate_no_match_returns_false():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    drawers = [
        {
            "id": "d1",
            "content": "RESEARCH:other_hash|...",
            "metadata": {"last_updated": now.isoformat()},
        }
    ]
    mod = _fake_mempalace(drawers)
    cand = Candidate(
        topic_hash("Foo", "2026-05-20"),
        "todoist",
        "T1",
        "Foo",
        "",
        "2026-05-20",
        "shallow",
    )
    assert is_duplicate(mod, cand, now=now) is False


def test_is_duplicate_missing_timestamp_dedupes_conservatively():
    now = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    h = topic_hash("Foo", "2026-05-20")
    drawers = [
        {"id": "d1", "content": f"RESEARCH:{h}|...", "metadata": {}}
    ]
    mod = _fake_mempalace(drawers)
    cand = Candidate(h, "todoist", "T1", "Foo", "", "2026-05-20", "shallow")
    assert is_duplicate(mod, cand, now=now) is True


# ---------- mempalace flag candidates -----------------------------------


def test_fetch_mempalace_flag_candidates_skips_triggered():
    now = datetime(2026, 5, 14, tzinfo=timezone.utc)
    drawers = [
        {
            "id": "d1",
            "content": "Research X before demo",
            "metadata": {
                "title": "Research X",
                "target_date": "2026-05-18",
                "triggered": "false",
            },
        },
        {
            "id": "d2",
            "content": "Already done",
            "metadata": {
                "title": "Done thing",
                "target_date": "2026-05-18",
                "triggered": "true",
            },
        },
    ]
    mod = _fake_mempalace(drawers)
    out = fetch_mempalace_flag_candidates(mod, today=now)
    assert len(out) == 1
    assert out[0].title == "Research X"
    assert out[0].source == "mempalace"


# ---------- killswitches -----------------------------------------------


def test_killswitch_disables_scanner(monkeypatch):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "0")
    assert run() == 0


def _one_todoist_candidate():
    return Candidate(
        topic_hash("Demo with Acme", "2026-05-18"),
        "todoist",
        "T1",
        "Demo with Acme",
        "Big pitch.",
        "2026-05-18",
        "shallow",
    )


def test_spawn_killswitch_inline_emits_empty_list_but_writes_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "1")
    monkeypatch.setenv("IGA_PROACTIVE_SPAWN", "0")
    monkeypatch.setenv("IGA_RUN_MODE", "inline")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    qfile = tmp_path / "queue.json"
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(qfile))

    with mock.patch.object(
        scanner, "fetch_todoist_candidates", return_value=[_one_todoist_candidate()]
    ):
        mod = _fake_mempalace([])
        buf = io.StringIO()
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            stdout=buf,
        )
    assert rc == 0
    assert qfile.is_file()
    payload = json.loads(qfile.read_text())
    assert len(payload) == 1
    assert payload[0]["title"] == "Demo with Acme"
    # stdout: emitted empty WORKER_REQUEST list
    emitted = json.loads(buf.getvalue().strip())
    assert emitted == []


def test_spawn_killswitch_daemon_runs_no_subprocess(monkeypatch, tmp_path):
    monkeypatch.setenv("IGA_PROACTIVE_RESEARCH", "1")
    monkeypatch.setenv("IGA_PROACTIVE_SPAWN", "0")
    monkeypatch.setenv("IGA_RUN_MODE", "daemon")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    qfile = tmp_path / "queue.json"
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(qfile))

    called = {"n": 0}

    def fake_runner(*a, **kw):  # pragma: no cover - should never run
        called["n"] += 1
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(
        scanner, "fetch_todoist_candidates", return_value=[_one_todoist_candidate()]
    ):
        mod = _fake_mempalace([])
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            worker_runner=fake_runner,
        )
    assert rc == 0
    assert called["n"] == 0
    assert qfile.is_file()


# ---------- queue roundtrip --------------------------------------------


def test_queue_write_read_roundtrip(tmp_path):
    qfile = tmp_path / "subdir" / "queue.json"
    cands = [
        Candidate(
            "abc1234567890def",
            "todoist",
            "T1",
            "Demo",
            "ctx",
            "2026-05-20",
            "shallow",
        ),
        Candidate(
            "abc1234567890dee",
            "mempalace",
            "M1",
            "Plan trip",
            "ctx2",
            "2026-05-22",
            "deep",
        ),
    ]
    write_queue(cands, path=qfile)
    out = read_queue(path=qfile)
    assert len(out) == 2
    assert out[0]["topic_hash"] == "abc1234567890def"
    assert out[1]["depth"] == "deep"
    assert out[0]["spawned_at"] is None
    assert out[0]["completed_at"] is None


# ---------- run mode switching -----------------------------------------


def _five_todoist_candidates():
    return [
        Candidate(
            topic_hash(f"Topic {i}", "2026-05-20"),
            "todoist",
            f"T{i}",
            f"Topic {i}",
            "",
            "2026-05-20",
            "shallow",
        )
        for i in range(5)
    ]


def test_invalid_run_mode_returns_4(monkeypatch, tmp_path):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.setenv("IGA_RUN_MODE", "garbage")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(tmp_path / "queue.json"))

    rc = run(now=datetime(2026, 5, 14, tzinfo=timezone.utc), mempalace_mod=_fake_mempalace([]))
    assert rc == 4


def test_inline_mode_emits_worker_requests_no_subprocess(monkeypatch, tmp_path):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.delenv("IGA_RESEARCH_DRY_RUN", raising=False)
    monkeypatch.setenv("IGA_RUN_MODE", "inline")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    qfile = tmp_path / "queue.json"
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(qfile))

    cands = _five_todoist_candidates()
    runner_calls: list = []

    def fake_runner(cmd, **kw):  # pragma: no cover - must NOT be called
        runner_calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(scanner, "fetch_todoist_candidates", return_value=cands):
        mod = _fake_mempalace([])
        buf = io.StringIO()
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            worker_runner=fake_runner,
            stdout=buf,
        )
    assert rc == 0
    # No subprocess invoked in inline mode.
    assert runner_calls == []
    emitted = json.loads(buf.getvalue().strip())
    # Capped at MAX_SPAWN_PER_TICK = 3.
    assert len(emitted) == 3
    first = emitted[0]
    assert set(first.keys()) >= {
        "topic_hash",
        "title",
        "context",
        "target_date",
        "depth",
        "source",
        "source_id",
        "worker_prompt_path",
    }
    assert first["worker_prompt_path"].endswith("worker.prompt.md")
    # All 5 stay in the queue file.
    payload = json.loads(qfile.read_text())
    assert len(payload) == 5
    # First 3 have spawned_at stamped; rest stay None.
    stamped = sum(1 for p in payload if p["spawned_at"] is not None)
    assert stamped == 3
    not_stamped = sum(1 for p in payload if p["spawned_at"] is None)
    assert not_stamped == 2


def test_daemon_mode_calls_subprocess_with_expected_args(monkeypatch, tmp_path):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.delenv("IGA_RESEARCH_DRY_RUN", raising=False)
    monkeypatch.setenv("IGA_RUN_MODE", "daemon")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    qfile = tmp_path / "queue.json"
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(qfile))

    # Worker prompt file must exist for daemon mode.
    prompt = tmp_path / "worker.prompt.md"
    prompt.write_text("test prompt body")
    monkeypatch.setattr(scanner, "WORKER_PROMPT_PATH", str(prompt))

    cands = _five_todoist_candidates()
    calls = []

    def fake_runner(cmd, **kw):
        calls.append({"cmd": cmd, "kw": kw})
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(scanner, "fetch_todoist_candidates", return_value=cands):
        mod = _fake_mempalace([])
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            worker_runner=fake_runner,
        )
    assert rc == 0
    # Hard cap at 3.
    assert len(calls) == 3
    for entry in calls:
        cmd = entry["cmd"]
        assert cmd[0] == "claude"
        assert "--bare" not in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--model" in cmd
        assert "--tools" in cmd
        assert "-p" in cmd
        # Prompt text is passed via -p
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "test prompt body"
        assert "--session-id" in cmd
        sid_idx = cmd.index("--session-id")
        import uuid as _uuid
        _uuid.UUID(cmd[sid_idx + 1])  # must be a valid UUID
        # Candidate JSON delivered on stdin.
        assert "input" in entry["kw"]
        json.loads(entry["kw"]["input"])


def test_max_spawn_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.setenv("IGA_RUN_MODE", "inline")
    monkeypatch.setenv("IGA_MAX_SPAWN_PER_TICK", "1")
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(tmp_path / "queue.json"))

    with mock.patch.object(
        scanner, "fetch_todoist_candidates", return_value=_five_todoist_candidates()
    ):
        mod = _fake_mempalace([])
        buf = io.StringIO()
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            stdout=buf,
        )
    assert rc == 0
    emitted = json.loads(buf.getvalue().strip())
    assert len(emitted) == 1


def test_inline_mode_is_default(monkeypatch, tmp_path):
    monkeypatch.delenv("IGA_RUN_MODE", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_RESEARCH", raising=False)
    monkeypatch.delenv("IGA_PROACTIVE_SPAWN", raising=False)
    monkeypatch.setenv("TODOIST_API_TOKEN", "fake")
    monkeypatch.setenv("IGA_RESEARCH_QUEUE_PATH", str(tmp_path / "queue.json"))

    runner_calls: list = []

    def fake_runner(cmd, **kw):  # pragma: no cover
        runner_calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(
        scanner, "fetch_todoist_candidates", return_value=[_one_todoist_candidate()]
    ):
        mod = _fake_mempalace([])
        buf = io.StringIO()
        rc = run(
            now=datetime(2026, 5, 14, tzinfo=timezone.utc),
            mempalace_mod=mod,
            worker_runner=fake_runner,
            stdout=buf,
        )
    assert rc == 0
    assert runner_calls == []  # no subprocess
    emitted = json.loads(buf.getvalue().strip())
    assert len(emitted) == 1

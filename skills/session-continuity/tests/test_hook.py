from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _session_fixtures import ENGINE, ca, claude_file, codex_file, cu, load, xu  # noqa: E402

hook = load("hook")
UTC = timezone.utc
NOON = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "IGA_SESSIONS_HARNESSES", "IGA_SESSIONS_REFRESH_MIN"):
        monkeypatch.delenv(name, raising=False)
    home, root = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    root.mkdir()
    return home, root


def prompt(ws, session_id="current", now=NOON, **extra):
    home, root = ws
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": session_id, "prompt": "hi", **extra}
    out = hook.process_event(payload, host="claude", root=root, state_dir=root / "state", now=now, home=home, tz=UTC)
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_first_prompt_lists_other_sessions_and_not_itself(ws):
    home, root = ws
    claude_file(home, root, "current", [cu("11:59", "what did we decide about paper?")])
    claude_file(home, root, "aaaa1111", [cu("09:00", "order more printer paper"), ca("09:05", "Moved.")])
    codex_file(home, "bbbb2222", root, [xu("10:00", "renew the domain")])
    text = prompt(ws)
    assert text.startswith("[Iga session continuity] Other sessions in this workspace today")
    assert "- 09:00-09:05 claude aaaa1111, 1 user turn(s): order more printer paper" in text
    assert "- 10:00-10:00 codex bbbb2222, 1 user turn(s): renew the domain" in text
    assert "what did we decide" not in text
    assert "sessions.py --session <id> --grep <word>" in text
    assert "Moved." not in text  # an index, never the conversation


def test_second_prompt_is_silent_until_refresh_and_new_activity(ws):
    home, root = ws
    other = claude_file(home, root, "aaaa1111", [cu("09:00", "first topic")])
    assert prompt(ws)
    assert prompt(ws, now=NOON + timedelta(minutes=5)) == ""
    # Refresh interval passed but nothing changed: still silent.
    assert prompt(ws, now=NOON + timedelta(minutes=61)) == ""
    with other.open("a") as stream:
        stream.write(json.dumps(cu("13:30", "second topic")) + "\n")
    assert prompt(ws, now=NOON + timedelta(minutes=70)) == ""  # too soon after the last check
    text = prompt(ws, now=NOON + timedelta(minutes=125))
    assert text.startswith("[Iga session continuity] Activity in other sessions")
    assert "claude aaaa1111" in text


def test_refresh_can_be_turned_off(ws, monkeypatch):
    home, root = ws
    other = claude_file(home, root, "aaaa1111", [cu("09:00", "first topic")])
    monkeypatch.setenv("IGA_SESSIONS_REFRESH_MIN", "0")
    assert prompt(ws)
    with other.open("a") as stream:
        stream.write(json.dumps(cu("13:30", "second topic")) + "\n")
    assert prompt(ws, now=NOON + timedelta(hours=5)) == ""


def test_each_session_gets_its_own_first_prompt(ws):
    home, root = ws
    claude_file(home, root, "aaaa1111", [cu("09:00", "first topic")])
    assert prompt(ws, session_id="one")
    assert prompt(ws, session_id="two")


def test_current_session_excluded_by_transcript_path(ws):
    home, root = ws
    mine = codex_file(home, "cccc3333", root, [xu("11:00", "this conversation")])
    assert prompt(ws, session_id="some-other-id", transcript_path=str(mine)) == ""


def test_silent_when_there_is_nothing_to_say(ws):
    home, root = ws
    claude_file(home, root, "current", [cu("11:59", "only me today")])
    assert prompt(ws) == ""
    assert prompt(ws, session_id="x", hook_event_name="Stop") == ""


def test_unreadable_store_is_named_once(ws):
    home, root = ws
    (home / ".codex").mkdir()
    claude_file(home, root, "aaaa1111", [cu("09:00", "first topic")])
    text = prompt(ws)
    assert "⚠️ codex sessions unavailable (no sessions folder in the Codex data directory)" in text
    assert "claude aaaa1111" in text


def test_no_store_at_all_is_reported(ws):
    assert "⚠️ other sessions unavailable" in prompt(ws)
    assert prompt(ws, now=NOON + timedelta(hours=3)) == ""


def test_long_days_are_capped(ws):
    home, root = ws
    for n in range(12):
        claude_file(home, root, f"s{n:02d}aaaaaa", [cu(f"{8 + n // 6:02d}:{(n % 6) * 10:02d}", f"topic {n}")])
    text = prompt(ws)
    assert text.count("\n- ") == hook.MAX_LINES + 1
    assert "and 4 older one(s)" in text and "topic 11" in text and "topic 0" not in text


def run(ws, payload, **env_extra):
    home, root = ws
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IGA_", "CLAUDE_CONFIG", "CODEX_HOME"))}
    env.update(HOME=str(home), USERPROFILE=str(home), **env_extra)
    return subprocess.run(
        [sys.executable, str(ENGINE / "hook.py"), "--host", "codex", "--root", str(root),
         "--state-dir", str(root / "state")],
        input=payload if isinstance(payload, str) else json.dumps(payload), capture_output=True, text=True, env=env)


def test_command_line_contract_fail_open_and_kill_switch(ws):
    home, root = ws
    record = {"type": "user", "timestamp": datetime.now().astimezone().isoformat(),
              "message": {"content": "today's other talk"}}
    claude_file(home, root, "aaaa1111", [record])
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "current", "prompt": "hi"}

    off = run(ws, payload, IGA_SESSION_CONTINUITY_OFF="1")
    assert off.returncode == 0 and off.stdout == ""

    done = run(ws, payload)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit" and "claude aaaa1111" in out["additionalContext"]

    for broken in ("not json", "[]", json.dumps({"hook_event_name": "UserPromptSubmit"})):
        result = run(ws, broken)
        assert result.returncode == 0 and result.stdout == ""

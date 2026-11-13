from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _session_fixtures import DAY, ENGINE, ca, claude_file, codex_file, cu, load, ts, xa, xu  # noqa: E402

sessions = load("sessions")
UTC = timezone.utc


@pytest.fixture
def ws(tmp_path, monkeypatch):
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "IGA_SESSIONS_HARNESSES", "IGA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IGA_SESSIONS_HARNESSES", "claude codex")  # most tests cover both readers
    home, root = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    root.mkdir()
    return home, root


def collect(ws, **kwargs):
    home, root = ws
    kwargs.setdefault("day", DAY)
    return sessions.collect(root, home=home, tz=UTC, **kwargs)


def all_sessions(sources):
    return [s for source in sources for s in source.sessions]


def texts(session):
    return [(t.who, t.text) for t in session.turns]


def test_claude_keeps_typed_text_and_drops_harness_noise(ws):
    home, root = ws
    claude_file(home, root, "aaaa1111", [
        {"type": "custom-title", "customTitle": "Printer paper", "sessionId": "aaaa1111"},
        cu("09:00", "<system-reminder>injected</system-reminder>"),
        cu("09:01", "order more printer paper"),
        cu("09:02", [{"type": "tool_result", "content": "huge tool output"}]),
        ca("09:03", "Done. Two boxes ordered."),
        {"type": "assistant", "timestamp": ts("09:04"),
         "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        cu("09:05", "meta caveat", isMeta=True),
        cu("09:06", "subagent prompt", isSidechain=True),
        cu("09:07", "<command-name>/iga</command-name><command-args>eod</command-args>"),
        cu("09:08", "Stop hook feedback: rewrite"),
    ])
    (session,) = all_sessions(collect(ws))
    assert texts(session) == [
        ("User", "order more printer paper"),
        ("Iga", "Done. Two boxes ordered."),
        ("User", "/iga eod"),
    ]
    assert session.label() == "Printer paper" and session.user_turns == 2


def test_codex_reads_only_this_workspace_and_final_answers(ws, tmp_path):
    home, root = ws
    codex_file(home, "c0de0001", root, [
        xu("11:00", "# AGENTS.md instructions for somewhere\nrules", "<environment_context>x</environment_context>",
           "the team sync moved to Friday"),
        xa("11:01", "Looking at the calendar now.", phase="commentary"),
        xa("11:02", "Friday 14:00 is in the calendar."),
        {"timestamp": ts("11:03"), "type": "response_item",
         "payload": {"type": "function_call_output", "output": "tool output"}},
    ])
    codex_file(home, "c0de0002", tmp_path / "elsewhere", [xu("11:00", "other project")])
    codex_file(home, "c0de0003", root, [xu("11:00", "guardian check")], source={"subagent": {"other": "guardian"}})
    codex = next(s for s in collect(ws) if s.harness == "codex")
    assert codex.status == "ok" and codex.unreadable == 0
    (session,) = codex.sessions
    assert session.id == "c0de0001"
    assert texts(session) == [("User", "the team sync moved to Friday"), ("Iga", "Friday 14:00 is in the calendar.")]
    assert session.label() == "the team sync moved to Friday"


def test_unknown_wrappers_are_kept_and_reply_markers_stripped(ws):
    home, root = ws
    claude_file(home, root, "wrap", [
        cu("09:00", "<!-- reply 1 -->\nyes, cancel the old plan"),
        cu("09:01", "<pasted_content id=\"1\">the contract text</pasted_content>"),
        cu("09:02", "<task-notification>background job finished</task-notification>"),
        cu("09:03", "<local-command-stdout>ok</local-command-stdout>"),
    ])
    (session,) = all_sessions(collect(ws))
    assert [t.text for t in session.turns] == [
        "yes, cancel the old plan",
        "<pasted_content id=\"1\">the contract text</pasted_content>",
    ]
    assert session.label() == "yes, cancel the old plan"


def test_archived_codex_sessions_are_read(ws):
    home, root = ws
    codex_file(home, "c0de0009", root, [xu("11:00", "archived talk")], folder="archived_sessions")
    assert [s.id for s in all_sessions(collect(ws))] == ["c0de0009"]


def test_day_filter_keeps_only_that_days_turns(ws):
    home, root = ws
    claude_file(home, root, "span", [
        {"type": "user", "timestamp": "2026-03-03T23:50:00Z", "message": {"content": "yesterday"}},
        cu("00:10", "today"),
    ])
    claude_file(home, root, "old", [
        {"type": "user", "timestamp": "2026-03-03T10:00:00Z", "message": {"content": "only yesterday"}}])
    (session,) = all_sessions(collect(ws))
    assert session.id == "span" and texts(session) == [("User", "today")]


def test_since_returns_later_turns_across_days(ws):
    home, root = ws
    claude_file(home, root, "late", [cu("21:00", "before the diary"), cu("23:30", "after the diary")])
    since = datetime(2026, 3, 4, 22, 0, tzinfo=UTC)
    (session,) = all_sessions(collect(ws, day=None, since=since))
    assert texts(session) == [("User", "after the diary")]


def test_current_session_is_excluded_by_id_or_path(ws):
    home, root = ws
    mine = claude_file(home, root, "mine", [cu("09:00", "this conversation")])
    claude_file(home, root, "other", [cu("10:00", "another conversation")])
    assert [s.id for s in all_sessions(collect(ws, exclude=["mine"]))] == ["other"]
    assert [s.id for s in all_sessions(collect(ws, exclude=[str(mine)]))] == ["other"]


def test_forked_session_keeps_only_its_new_turns(ws):
    home, root = ws
    shared = [cu("09:00", "first"), ca("09:01", "reply")]
    parent = claude_file(home, root, "parent", shared)
    os.utime(parent, (1, parent.stat().st_mtime - 60))
    claude_file(home, root, "fork", [*shared, cu("12:00", "only in the fork")])
    by_id = {s.id: texts(s) for s in all_sessions(collect(ws))}
    assert by_id == {"parent": [("User", "first"), ("Iga", "reply")], "fork": [("User", "only in the fork")]}
    # The same holds when the parent is the current, excluded session.
    assert {s.id: texts(s) for s in all_sessions(collect(ws, exclude=["parent"]))} == {
        "fork": [("User", "only in the fork")]}


def test_missing_harness_is_reported_not_hidden(ws):
    home, root = ws
    claude_file(home, root, "only", [cu("09:00", "hello there")])
    by_name = {s.harness: s for s in collect(ws)}
    assert by_name["claude"].line() == "claude: ok, 1 session(s)"
    assert by_name["codex"].status == "absent" and "not installed" in by_name["codex"].line()
    assert sessions.reviewable(list(by_name.values()))


def test_store_without_sessions_folder_is_unavailable(ws):
    home, root = ws
    (home / ".codex").mkdir()
    (home / ".claude").mkdir()
    lines = [s.line() for s in collect(ws)]
    assert lines == [
        "⚠️ claude sessions unavailable (no projects folder in the Claude Code data directory)",
        "⚠️ codex sessions unavailable (no sessions folder in the Codex data directory)",
    ]
    assert not sessions.reviewable(collect(ws))


def test_unknown_format_is_counted_unreadable(ws):
    home, root = ws
    claude_file(home, root, "good", [cu("09:00", "fine")])
    slug_dir = home / ".claude" / "projects" / sessions.claude_slug(root)
    (slug_dir / "garbage.jsonl").write_text("not json at all\nstill not\n")
    (slug_dir / "empty.jsonl").write_text("")
    bad = home / ".codex" / "sessions" / "2026" / "03" / "04"
    bad.mkdir(parents=True)
    (bad / "rollout-x.jsonl").write_text(json.dumps({"type": "something_new", "payload": {"a": 1}}) + "\n")
    by_name = {s.harness: s for s in collect(ws)}
    assert by_name["claude"].unreadable == 1 and len(by_name["claude"].sessions) == 1
    assert "⚠️ 1 claude file(s) unreadable" in by_name["claude"].line()
    assert by_name["codex"].unreadable == 1 and "⚠️" in by_name["codex"].line()


def test_harness_selection_from_env(ws, monkeypatch):
    home, root = ws
    claude_file(home, root, "c", [cu("09:00", "from claude")])
    codex_file(home, "x", root, [xu("09:30", "from codex")])
    assert {s.harness for s in collect(ws)} == {"claude", "codex"}
    monkeypatch.setenv("IGA_SESSIONS_HARNESSES", "claude")
    assert [s.harness for s in collect(ws)] == ["claude"]
    monkeypatch.delenv("IGA_SESSIONS_HARNESSES")
    assert [s.harness for s in collect(ws)] == ["claude"]  # Codex is opt-in


def test_claude_slug_matches_the_harness_folder_naming():
    assert sessions.claude_slug(Path("/srv/work/My_Iga/.claude/worktrees/x")) == "-srv-work-My-Iga--claude-worktrees-x"


FAKE = {
    "api key": "sk-" + "a1B2" * 6,
    "github token": "gh" + "p_" + "A1b2C3d4E5" * 3,
    "slack token": "xo" + "xb-" + "1234567890-abcdefghij",
    "aws key id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "jwt": "ey" + "J" + "hbGciOiJIUzI1NiIsInR5cCI6" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".sig",
    "database url": "postgres://app:" + "hunter2hunter2" + "@db.example.com:5432/app",
    "bearer": "Authorization: Bearer " + "abcDEF123456" * 2,
    "assignment": "password=" + "correct-horse-battery",
}


@pytest.mark.parametrize("name", sorted(FAKE))
def test_credentials_are_masked(name):
    secret = FAKE[name]
    masked = sessions.mask(f"here it is: {secret} ok")
    assert "[secret]" in masked
    assert secret.split(" ")[-1][-12:] not in masked


@pytest.mark.parametrize("text", [
    "see https://example.com:8080/path?mail=a@b.example for details",
    "the token ring network and a secret santa",
    "meeting at 14:00, password policy review",
])
def test_ordinary_text_is_not_masked(text):
    assert sessions.mask(text) == text


def test_secret_in_a_transcript_never_reaches_the_output(ws):
    home, root = ws
    claude_file(home, root, "leak", [cu("09:00", "my key is " + FAKE["api key"])])
    (session,) = all_sessions(collect(ws))
    assert FAKE["api key"] not in session.turns[0].text and "[secret]" in session.turns[0].text


def run_cli(ws, *args, **env_extra):
    home, root = ws
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IGA_", "CLAUDE_", "CODEX_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), IGA_HOME=str(root), TZ="UTC",
               IGA_SESSIONS_HARNESSES="claude codex")
    env.update(env_extra)
    return subprocess.run([sys.executable, str(ENGINE / "sessions.py"), *args],
                          capture_output=True, text=True, env=env)


def test_cli_excludes_the_current_session_when_the_harness_names_it(ws):
    home, root = ws
    claude_file(home, root, "mine-0001", [cu("09:00", "this conversation")])
    claude_file(home, root, "other-0002", [cu("10:00", "another conversation")])
    named = run_cli(ws, "--list", "--day", DAY, "--exclude-current", CLAUDE_CODE_SESSION_ID="mine-0001")
    assert "this conversation" not in named.stdout and "another conversation" in named.stdout
    assert "note:" not in named.stdout
    unnamed = run_cli(ws, "--list", "--day", DAY, "--exclude-current")
    assert "this conversation" in unnamed.stdout
    assert "note: this harness does not expose the current session's id" in unnamed.stdout


def test_cli_list_extract_and_single_session(ws, tmp_path):
    home, root = ws
    claude_file(home, root, "abcdef12-0000", [cu("09:00", "water the plants"), ca("09:01", "Watered on Tuesday.")])
    codex_file(home, "fedcba98-1111", root, [xu("10:00", "renew the domain"), xa("10:01", "Renewed.")])

    listing = run_cli(ws, "--list", "--day", DAY)
    assert listing.returncode == 0, listing.stderr
    assert listing.stdout.splitlines() == [
        "claude: ok, 1 session(s)",
        "codex: ok, 1 session(s)",
        "09:00-09:01 claude abcdef12, 1 user turn(s): water the plants",
        "10:00-10:01 codex fedcba98, 1 user turn(s): renew the domain",
    ]

    as_json = json.loads(run_cli(ws, "--list", "--json", "--day", DAY).stdout)
    assert [s["status"] for s in as_json["sources"]] == ["ok", "ok"]
    assert [s["id"] for s in as_json["sessions"]] == ["abcdef12-0000", "fedcba98-1111"]

    out_dir = tmp_path / "out"
    extract = run_cli(ws, "--day", DAY, "--exclude", "abcdef12-0000", "--out-dir", str(out_dir))
    written = out_dir / "codex-fedcba98-1111.txt"
    assert extract.stdout.splitlines()[-1] == f"{written}\t2"
    assert written.read_text().splitlines() == [
        "=== codex session fedcba98-1111 (2 turns)",
        f"{DAY} 10:00 User: renew the domain",
        f"{DAY} 10:01 Iga: Renewed.",
    ]
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert not (out_dir / "claude-abcdef12-0000.txt").exists()

    one = run_cli(ws, "--session", "abcdef12", "--grep", "WATERED")
    assert f"{DAY} 09:01 Iga: Watered on Tuesday." in one.stdout
    assert "water the plants" not in one.stdout and "renew the domain" not in one.stdout


def test_cli_says_so_when_nothing_can_be_read(ws):
    result = run_cli(ws, "--list", "--day", DAY)
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "claude: not installed (no Claude Code data directory)",
        "codex: not installed (no Codex data directory)",
        "⚠️ other sessions unavailable (no transcript store could be read)",
    ]


def test_cli_requires_a_scope(ws):
    assert run_cli(ws, "--list").returncode == 2

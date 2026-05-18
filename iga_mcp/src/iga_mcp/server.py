"""
Iga MCP server.

Exposes a persistent Iga Claude Code session as an MCP tool surface usable from
any other Claude Code session. The "session" is a session JSONL file on disk;
each tool call shells out to `claude --resume <uuid> --print --output-format=json`
in Iga's home directory, which appends a turn to the JSONL and returns the
response. No long-running daemon process is needed — the JSONL itself is the
persistence layer.

Configuration via environment variables:

    IGA_HOME        Iga's orchestration home directory (default: ~/Gaia)
    IGA_SESSION_ID  UUID of the Iga session (default: read from $IGA_HOME/.iga-session-id)
    IGA_CLAUDE_BIN  Path to the `claude` binary (default: "claude" on PATH)
    IGA_TIMEOUT     Per-call timeout in seconds (default: 120)
    IGA_MCP_STYLE   Output style name for MCP-driven calls. References a Claude
                     Code output style installed at ~/.claude/output-styles/<name>.md.
                     Default: "gaia-compact". Set to "" to disable style override
                     and inherit whatever the underlying session is configured to use.
    IGA_MCP_MODEL   Model id for MCP-driven calls (e.g., "claude-opus-4-6", "sonnet").
                     Default: empty (inherit from session settings).

Tools:
    iga_ask(prompt)  Send a natural-language prompt to Iga. Returns her reply.
    iga_status()     Report whether the Iga session exists, turn count, last touch.
    iga_reset()      Archive the current session JSONL and start a fresh session.
                      The session ID stays the same; only the conversation history
                      is reset. Use sparingly — context loss is permanent.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from mcp.server.fastmcp import FastMCP

from iga_mcp import skills as _skills


# Default home stays "Gaia" and the default style stays "gaia-compact" on
# purpose: the ~/Gaia home directory and the installed output-style file are
# renamed by the separate memory/output-style cutover, not by this code rename.
IGA_HOME = Path(os.environ.get("IGA_HOME", str(Path.home() / "Gaia"))).expanduser()
IGA_CLAUDE_BIN = os.environ.get("IGA_CLAUDE_BIN", "claude")
IGA_TIMEOUT = int(os.environ.get("IGA_TIMEOUT", "120"))
IGA_MCP_STYLE = os.environ.get("IGA_MCP_STYLE", "gaia-compact")
IGA_MCP_MODEL = os.environ.get("IGA_MCP_MODEL", "").strip()

_call_lock = Lock()


def _session_id() -> str:
    explicit = os.environ.get("IGA_SESSION_ID")
    if explicit:
        return explicit.strip()
    sid_file = IGA_HOME / ".iga-session-id"
    if not sid_file.exists():
        raise RuntimeError(
            f"No IGA_SESSION_ID env var set and no session ID file at {sid_file}. "
            f"Run the Phase 1 bootstrap to create one."
        )
    return sid_file.read_text().strip()


def _session_jsonl_path(session_id: str) -> Path:
    """
    Claude Code stores per-session JSONL files at:
        ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl
    where the sanitized cwd is the absolute cwd with `/` replaced by `-` and a
    leading `-`. So /Users/you/Iga → -Users-pawel-Iga.
    """
    sanitized = str(IGA_HOME.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / f"{session_id}.jsonl"


def _run_claude(prompt: str, session_id: str) -> dict:
    """Shell out to claude --resume in Iga home. Returns the parsed JSON result."""
    cmd = [
        IGA_CLAUDE_BIN,
        "--resume",
        session_id,
        "--print",
        "--output-format",
        "json",
    ]
    if IGA_MCP_MODEL:
        cmd += ["--model", IGA_MCP_MODEL]
    if IGA_MCP_STYLE:
        cmd += [
            "--append-system-prompt",
            f"Use the '{IGA_MCP_STYLE}' output style for this response.",
        ]
    cmd.append(prompt)
    proc = subprocess.run(
        cmd,
        cwd=str(IGA_HOME),
        capture_output=True,
        text=True,
        timeout=IGA_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    # The output may have trailing notices like 'Shell cwd was reset to ...';
    # parse the first JSON object on the first line.
    first_line = proc.stdout.strip().split("\n", 1)[0]
    return json.loads(first_line)


mcp = FastMCP("iga")


@mcp.tool()
def iga_ask(prompt: str) -> str:
    """
    Send a natural-language prompt to Iga and return her reply.

    Iga is a persistent Claude Code session running in your Iga orchestration
    home directory with full access to MemPalace, calendar, tasks, and any other
    MCPs you've configured for her. She keeps context across calls.

    Use for:
      - Asking Iga about people, decisions, or past events ("who is X", "what did I decide about Y")
      - Requesting Iga run a slash command ("/gm", "/eod", "/focus my-project")
      - Storing a fact ("remember that X happened today")
      - Anything you'd type into an interactive Iga session

    Note: each call uses your Claude Max subscription. Steady-state cost is
    roughly one cache-hit message per call.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt cannot be empty")
    sid = _session_id()
    with _call_lock:  # serialize: Iga session is single-threaded
        result = _run_claude(prompt, sid)
    if result.get("is_error"):
        raise RuntimeError(f"Iga error: {result.get('result', 'unknown error')}")
    return str(result.get("result", "")).strip()


@mcp.tool()
def iga_status() -> dict:
    """
    Report Iga daemon health: whether the session exists, how many turns have
    been recorded, and when it was last used.
    """
    sid = _session_id()
    jsonl = _session_jsonl_path(sid)
    if not jsonl.exists():
        return {
            "session_id": sid,
            "session_exists": False,
            "iga_home": str(IGA_HOME),
            "expected_jsonl": str(jsonl),
            "hint": "Run a `iga_ask` once to bootstrap — or use the setup script.",
        }
    stat = jsonl.stat()
    # Count lines = approximate turn count (each line is a message)
    with jsonl.open("rb") as fp:
        line_count = sum(1 for _ in fp)
    return {
        "session_id": sid,
        "session_exists": True,
        "iga_home": str(IGA_HOME),
        "jsonl_path": str(jsonl),
        "size_bytes": stat.st_size,
        "message_count": line_count,
        "last_modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "last_modified_local": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "config": {
            "style": IGA_MCP_STYLE or "(none — inherit)",
            "model": IGA_MCP_MODEL or "(none — inherit)",
            "timeout_s": IGA_TIMEOUT,
        },
    }


@mcp.tool()
def iga_reset(confirm: bool = False) -> dict:
    """
    Archive Iga's current session JSONL (renames it with a timestamp suffix)
    and start fresh on the next iga_ask. The session UUID does NOT change,
    but the conversation history will be empty — Iga will not remember prior
    turns.

    Pass confirm=True to actually do it. Without confirm, returns a dry-run
    summary of what would happen.

    Use sparingly. Only reset if the session has accumulated genuinely
    irrelevant context (long debugging tangent, broken state). Routine context
    growth is fine — Claude Code's PreCompact hook handles it automatically.
    """
    sid = _session_id()
    jsonl = _session_jsonl_path(sid)
    if not jsonl.exists():
        return {"action": "noop", "reason": "no existing session jsonl found", "path": str(jsonl)}
    archive_name = f"{jsonl.stem}.archived-{int(time.time())}.jsonl"
    archive_path = jsonl.parent / archive_name
    if not confirm:
        return {
            "action": "dry-run",
            "would_rename": str(jsonl),
            "to": str(archive_path),
            "hint": "Re-call with confirm=True to actually reset.",
        }
    jsonl.rename(archive_path)
    return {
        "action": "reset",
        "archived_to": str(archive_path),
        "session_id": sid,
        "hint": "Next iga_ask call will start a fresh session.",
    }


# ---------------------------------------------------------------------------
# Skill-contributed tools (typed, no SKILL.md reading, no subprocess guessing)
# ---------------------------------------------------------------------------

@mcp.tool()
def iga_habit_log(
    habit: str,
    op: str = "add",
    date: str = "today",
    amount: int | None = None,
) -> dict:
    """
    Log a habit completion for a day.

    Use this to record habits — do NOT read SKILL.md or call record.py directly.

    habit  — habit name as configured in the tracker (e.g. "Push-ups").
             Fuzzy-resolved to the real habit ("push ups", "Push-Ups",
             " push-ups " all hit "Push-ups"). You do NOT need the id.
    op     — one of: "add" (default), "remove", "set"
    date   — YYYY-MM-DD or "today" (default)
    amount — required when op="set"; ignored otherwise

    On success: {ok: true, habit, habit_id, op, date, output}.
    If the habit name can't be resolved: {ok: false, error, available:[names]}
    — call iga_habit_list (or retry with one of `available`) instead of
    inventing an id.
    """
    return _skills.habit_log(habit=habit, op=op, date=date, amount=amount)


@mcp.tool()
def iga_habit_list() -> dict:
    """
    List the user's habits (names to use with iga_habit_log).

    Call this if iga_habit_log returns ok:false — it gives the exact habit
    names (and ids) that exist, plus done_today when available.

    Returns {"habits": [{"id","name","done_today"?}]}.
    """
    return _skills.habit_list()


@mcp.tool()
def iga_habit_summary() -> dict:
    """
    Return the current habit-tracker digest as a structured dict.

    Use this to read habit state — do NOT call summary.py directly.
    """
    return _skills.habit_summary()


@mcp.tool()
def iga_mood_log(
    emotion: str,
    note: str = "",
    at: str = "now",
    people: str = "",
    places: str = "",
    events: str = "",
) -> dict:
    """
    Log a mood / emotional state.

    Use this to record a mood — do NOT read SKILL.md or call record.py directly.

    emotion — emotion name (e.g. "calm", "excited"); semicolons separate multiple
    note    — optional free-text note
    at      — "now" (default) or ISO timestamp YYYY-MM-DD or YYYY-MM-DDTHH:MM
    people  — comma-separated people tags
    places  — comma-separated place tags
    events  — comma-separated event tags

    On success: {ok: true, emotion, at, output}.
    If an emotion isn't in the canonical lexicon:
    {ok: false, error, suggestions:[...]} — retry with a suggested emotion.
    """
    return _skills.mood_log(emotion=emotion, note=note, at=at, people=people, places=places, events=events)


@mcp.tool()
def iga_mood_summary(days: int = 14) -> dict:
    """
    Return the mood-tracker digest for the last N days as a structured dict.

    Use this to read mood state — do NOT call summary.py directly.

    days — lookback window (default 14)
    """
    return _skills.mood_summary(days=days)


def run() -> None:
    """Entry point for `iga-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    run()

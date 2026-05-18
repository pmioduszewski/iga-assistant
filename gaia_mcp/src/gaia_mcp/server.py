"""
Gaia MCP server.

Exposes a persistent Gaia Claude Code session as an MCP tool surface usable from
any other Claude Code session. The "session" is a session JSONL file on disk;
each tool call shells out to `claude --resume <uuid> --print --output-format=json`
in Gaia's home directory, which appends a turn to the JSONL and returns the
response. No long-running daemon process is needed — the JSONL itself is the
persistence layer.

Configuration via environment variables:

    GAIA_HOME        Gaia's orchestration home directory (default: ~/Gaia)
    GAIA_SESSION_ID  UUID of the Gaia session (default: read from $GAIA_HOME/.gaia-session-id)
    GAIA_CLAUDE_BIN  Path to the `claude` binary (default: "claude" on PATH)
    GAIA_TIMEOUT     Per-call timeout in seconds (default: 120)
    GAIA_MCP_STYLE   Output style name for MCP-driven calls. References a Claude
                     Code output style installed at ~/.claude/output-styles/<name>.md.
                     Default: "gaia-compact". Set to "" to disable style override
                     and inherit whatever the underlying session is configured to use.
    GAIA_MCP_MODEL   Model id for MCP-driven calls (e.g., "claude-opus-4-6", "sonnet").
                     Default: empty (inherit from session settings).

Tools:
    gaia_ask(prompt)  Send a natural-language prompt to Gaia. Returns her reply.
    gaia_status()     Report whether the Gaia session exists, turn count, last touch.
    gaia_reset()      Archive the current session JSONL and start a fresh session.
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


GAIA_HOME = Path(os.environ.get("GAIA_HOME", str(Path.home() / "Gaia"))).expanduser()
GAIA_CLAUDE_BIN = os.environ.get("GAIA_CLAUDE_BIN", "claude")
GAIA_TIMEOUT = int(os.environ.get("GAIA_TIMEOUT", "120"))
GAIA_MCP_STYLE = os.environ.get("GAIA_MCP_STYLE", "gaia-compact")
GAIA_MCP_MODEL = os.environ.get("GAIA_MCP_MODEL", "").strip()

_call_lock = Lock()


def _session_id() -> str:
    explicit = os.environ.get("GAIA_SESSION_ID")
    if explicit:
        return explicit.strip()
    sid_file = GAIA_HOME / ".gaia-session-id"
    if not sid_file.exists():
        raise RuntimeError(
            f"No GAIA_SESSION_ID env var set and no session ID file at {sid_file}. "
            f"Run the Phase 1 bootstrap to create one."
        )
    return sid_file.read_text().strip()


def _session_jsonl_path(session_id: str) -> Path:
    """
    Claude Code stores per-session JSONL files at:
        ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl
    where the sanitized cwd is the absolute cwd with `/` replaced by `-` and a
    leading `-`. So /Users/you/Gaia → -Users-pawel-Gaia.
    """
    sanitized = str(GAIA_HOME.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / f"{session_id}.jsonl"


def _run_claude(prompt: str, session_id: str) -> dict:
    """Shell out to claude --resume in Gaia home. Returns the parsed JSON result."""
    cmd = [
        GAIA_CLAUDE_BIN,
        "--resume",
        session_id,
        "--print",
        "--output-format",
        "json",
    ]
    if GAIA_MCP_MODEL:
        cmd += ["--model", GAIA_MCP_MODEL]
    if GAIA_MCP_STYLE:
        cmd += [
            "--append-system-prompt",
            f"Use the '{GAIA_MCP_STYLE}' output style for this response.",
        ]
    cmd.append(prompt)
    proc = subprocess.run(
        cmd,
        cwd=str(GAIA_HOME),
        capture_output=True,
        text=True,
        timeout=GAIA_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    # The output may have trailing notices like 'Shell cwd was reset to ...';
    # parse the first JSON object on the first line.
    first_line = proc.stdout.strip().split("\n", 1)[0]
    return json.loads(first_line)


mcp = FastMCP("gaia")


@mcp.tool()
def gaia_ask(prompt: str) -> str:
    """
    Send a natural-language prompt to Gaia and return her reply.

    Gaia is a persistent Claude Code session running in your Gaia orchestration
    home directory with full access to MemPalace, calendar, tasks, and any other
    MCPs you've configured for her. She keeps context across calls.

    Use for:
      - Asking Gaia about people, decisions, or past events ("who is X", "what did I decide about Y")
      - Requesting Gaia run a slash command ("/gm", "/eod", "/focus my-project")
      - Storing a fact ("remember that X happened today")
      - Anything you'd type into an interactive Gaia session

    Note: each call uses your Claude Max subscription. Steady-state cost is
    roughly one cache-hit message per call.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt cannot be empty")
    sid = _session_id()
    with _call_lock:  # serialize: Gaia session is single-threaded
        result = _run_claude(prompt, sid)
    if result.get("is_error"):
        raise RuntimeError(f"Gaia error: {result.get('result', 'unknown error')}")
    return str(result.get("result", "")).strip()


@mcp.tool()
def gaia_status() -> dict:
    """
    Report Gaia daemon health: whether the session exists, how many turns have
    been recorded, and when it was last used.
    """
    sid = _session_id()
    jsonl = _session_jsonl_path(sid)
    if not jsonl.exists():
        return {
            "session_id": sid,
            "session_exists": False,
            "gaia_home": str(GAIA_HOME),
            "expected_jsonl": str(jsonl),
            "hint": "Run a `gaia_ask` once to bootstrap — or use the setup script.",
        }
    stat = jsonl.stat()
    # Count lines = approximate turn count (each line is a message)
    with jsonl.open("rb") as fp:
        line_count = sum(1 for _ in fp)
    return {
        "session_id": sid,
        "session_exists": True,
        "gaia_home": str(GAIA_HOME),
        "jsonl_path": str(jsonl),
        "size_bytes": stat.st_size,
        "message_count": line_count,
        "last_modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "last_modified_local": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "config": {
            "style": GAIA_MCP_STYLE or "(none — inherit)",
            "model": GAIA_MCP_MODEL or "(none — inherit)",
            "timeout_s": GAIA_TIMEOUT,
        },
    }


@mcp.tool()
def gaia_reset(confirm: bool = False) -> dict:
    """
    Archive Gaia's current session JSONL (renames it with a timestamp suffix)
    and start fresh on the next gaia_ask. The session UUID does NOT change,
    but the conversation history will be empty — Gaia will not remember prior
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
        "hint": "Next gaia_ask call will start a fresh session.",
    }


def run() -> None:
    """Entry point for `gaia-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    run()

"""Tests for the Codex time hook. Run: pytest scripts/codex/hooks"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOOK = Path(__file__).with_name("time_context.py")


def _run(stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=10
    )


def test_reply_matches_the_codex_wire_format():
    proc = _run(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
    assert proc.returncode == 0
    reply = json.loads(proc.stdout)
    out = reply["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert out["additionalContext"].startswith("Current local time: ")


def test_garbage_stdin_never_blocks_the_prompt():
    proc = _run("not json at all")
    assert proc.returncode == 0
    assert "Current local time: " in json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_context_carries_weekday_and_utc():
    sys.path.insert(0, str(HOOK.parent))
    try:
        from time_context import build_context
    finally:
        sys.path.pop(0)
    text = build_context(datetime(2026, 1, 5, 12, 30, tzinfo=timezone.utc))
    assert "UTC 2026-01-05 12:30" in text
    local = datetime(2026, 1, 5, 12, 30, tzinfo=timezone.utc).astimezone()
    assert f"{local:%A}" in text

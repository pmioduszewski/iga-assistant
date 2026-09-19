#!/usr/bin/env python3
"""Codex UserPromptSubmit hook: tell the model the current local time.

Codex pipes the event JSON to stdin and reads a JSON reply from stdout; the
text under hookSpecificOutput.additionalContext is added to the turn. Python
(stdlib only) rather than shell so the same file works on Windows.

Any failure prints nothing and exits 0: a broken clock hook must never block
a prompt.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def build_context(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone()
    utc = local.astimezone(timezone.utc)
    zone = local.tzname() or local.strftime("%z")
    return (
        f"Current local time: {local:%Y-%m-%d %H:%M} {local:%A} {zone} "
        f"(UTC {utc:%Y-%m-%d %H:%M}). Treat this as ground truth for anything "
        "time-related; pass explicit timestamps to tools."
    )


def main() -> int:
    try:
        sys.stdin.read()  # drain the event payload; nothing in it is needed
        reply = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_context(),
            }
        }
        sys.stdout.write(json.dumps(reply))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

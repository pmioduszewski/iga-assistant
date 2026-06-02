#!/usr/bin/env python3
"""iga-guard LLM privacy judge — reliable, subscription-billed, hard-killable.

Classifies a staged diff / commit message as OK or BLOCK for a PUBLIC repo.

Backends (first that yields a verdict wins):
  1. GitHub Copilot CLI (`copilot -p`) — runs on the Copilot subscription, a
     SEPARATE pool from Claude (reliable, no nested-`claude -p` flakiness, never
     touches Claude limits). Primary.
  2. Nested `claude -p` on the Claude SUBSCRIPTION (OAuth /login, no API key —
     never pay-as-you-go; from 2026-06-15 draws the separate Agent-SDK credit).
     MCP servers skipped for speed. Fallback.

Reads payload on stdin, prints the verdict line on stdout. Empty => no verdict
=> caller fails closed.

KEY ROBUSTNESS: these CLIs sometimes PRINT the verdict and then fail to exit
cleanly (background children, daemons). `communicate()` waits for exit, so it
would hang the whole timeout and DISCARD the verdict it already printed. Instead
we read stdout incrementally and return the MOMENT a verdict line appears, then
SIGKILL the whole process group. So: never hangs past the deadline, and never
loses a verdict that was actually produced.
"""
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time

SYS_PROMPT = sys.argv[1] if len(sys.argv) > 1 else ""
TIMEOUT = int(os.environ.get("IGA_GUARD_TIMEOUT", "60"))
payload = sys.stdin.read()

_VERDICT = re.compile(rb"(?im)^\s*(OK|BLOCK)\b.*$")


def run_capped(cmd, env=None, stdin_data=None):
    """Run cmd in its own session. Return stdout once a verdict line appears, or
    at EOF/timeout. Always SIGKILLs the whole process group before returning."""
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, start_new_session=True,
        )
    except FileNotFoundError:
        return ""

    def _feed():
        try:
            if stdin_data:
                proc.stdin.write(stdin_data.encode("utf-8", "replace"))
            proc.stdin.close()
        except Exception:
            pass

    threading.Thread(target=_feed, daemon=True).start()

    fd = proc.stdout.fileno()
    deadline = time.time() + TIMEOUT
    buf = b""
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:           # EOF
                    break
                buf += chunk
                if _VERDICT.search(buf):  # verdict in hand — stop, don't wait for exit
                    break
            elif proc.poll() is not None:
                break
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    return buf.decode("utf-8", "replace")


def verdict(text):
    m = re.search(r"(?im)^\s*(OK|BLOCK)\b.*$", text or "")
    return m.group(0).strip() if m else ""


# Backend 1: GitHub Copilot CLI (separate subscription pool). Prompt via the -p arg
# (64 KB is well under ARG_MAX). The root cause of the in-hook hang: in `-p` mode,
# when the model reaches for a tool/path it tries to prompt y/n with no TTY and
# HANGS (github/copilot-cli#550). The SAFE fix is to give it ZERO tools — NOT
# --allow-all-tools (which would let an agent auto-run shell on diff content from a
# persistent hook). `--available-tools=` (empty) means no tools are available, so
# it can only answer text: nothing to permit, nothing to execute, no hang.
# Empirically consistent at ~6-9s. `--disable-builtin-mcps` drops the GitHub MCP too.
if shutil.which("copilot"):
    v = verdict(run_capped([
        "copilot", "-p", SYS_PROMPT + "\n\n" + payload,
        "--available-tools=", "--disable-builtin-mcps",
    ]))
    if v:
        print(v)
        sys.exit(0)

# Backend 2: nested `claude -p` on the Claude subscription (API key stripped).
if shutil.which("claude"):
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("ANTHROPIC_API_KEY", None)   # FORCE subscription OAuth — never paid API
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    cmd = [
        "claude", "-p",
        "--model", os.environ.get("IGA_GUARD_MODEL", "claude-haiku-4-5-20251001"),
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--append-system-prompt", SYS_PROMPT,
    ]
    v = verdict(run_capped(cmd, env=env, stdin_data=payload))
    if v:
        print(v)
        sys.exit(0)

# No verdict → empty stdout → caller fails closed.

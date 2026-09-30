#!/usr/bin/env python3
"""Merge this clone's response hooks into local host settings."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

EVENTS = ("UserPromptSubmit", "Stop")


def command(root: Path, host: str, max_words: int) -> str:
    args = [sys.executable, str(root / "skills/response-guard/engine/guard.py"),
            "--host", host, "--max-words", str(max_words), "--state-dir",
            str(root / "state/response-guard")]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def is_owned(handler: dict, root: Path, host: str) -> bool:
    raw = handler.get("command", "")
    if handler.get("type") != "command" or not isinstance(raw, str):
        return False
    path = str(root / "skills/response-guard/engine/guard.py")
    if os.name == "nt":
        return (f'"{path}"' in raw or f" {path} --host " in raw) and f"--host {host}" in raw
    try:
        args = shlex.split(raw)
        return path in args and args[args.index("--host") + 1] == host
    except (ValueError, IndexError):
        return False


def merge(data: dict, root: Path, host: str, max_words: int, uninstall: bool = False) -> dict:
    result = copy.deepcopy(data)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"{event} must be a list")
        retained = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"invalid {event} hook group")
            if any(not isinstance(h, dict) for h in group["hooks"]):
                raise ValueError("hook handler must be an object")
            handlers = [h for h in group["hooks"] if not is_owned(h, root, host)]
            if handlers or not group["hooks"]:
                retained.append({**group, "hooks": handlers})
        if not uninstall:
            retained.append({"hooks": [{"type": "command", "command": command(root, host, max_words), "timeout": 5}]})
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event, None)
    return result


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if path.exists():
            os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--host", choices=("claude", "codex", "both"), default="both")
    parser.add_argument("--max-words", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.max_words < 1:
        parser.error("--max-words must be positive")
    root = args.root.resolve()
    hosts = ("claude", "codex") if args.host == "both" else (args.host,)
    planned = []
    try:
        for host in hosts:
            path = root / (".claude/settings.local.json" if host == "claude" else ".codex/hooks.json")
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError(f"{path}: expected an object")
            updated = merge(data, root, host, args.max_words, args.uninstall)
            if updated != data:
                planned.append((path, updated))
        # Validate both targets before writing either.
        for path, updated in planned:
            if not args.dry_run:
                write_atomic(path, updated)
            print(f"{'Would update' if args.dry_run else 'Updated'} {path}")
        if not planned:
            print("Response guard settings already current.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Settings not installed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

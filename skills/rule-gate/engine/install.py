#!/usr/bin/env python3
"""Merge this clone's rule gate into Claude Code's local settings.

Claude Code only: the gate answers a PreToolUse event with a deny decision,
and that contract has not been verified for other hosts.
"""
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

EVENT = "PreToolUse"
MATCHER = "mcp__.*__mempalace_add_drawer"
HOOK = "skills/rule-gate/engine/gate.py"
TARGET = ".claude/settings.local.json"


def command(root: Path) -> str:
    args = [sys.executable, str(root / HOOK)]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def is_owned(handler: dict, root: Path) -> bool:
    raw = handler.get("command", "")
    return handler.get("type") == "command" and isinstance(raw, str) and str(root / HOOK) in raw


def merge(data: dict, root: Path, uninstall: bool = False) -> dict:
    result = copy.deepcopy(data)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    groups = hooks.get(EVENT, [])
    if not isinstance(groups, list):
        raise ValueError(f"{EVENT} must be a list")
    retained = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError(f"invalid {EVENT} hook group")
        if any(not isinstance(h, dict) for h in group["hooks"]):
            raise ValueError("hook handler must be an object")
        handlers = [h for h in group["hooks"] if not is_owned(h, root)]
        if handlers or not group["hooks"]:
            retained.append({**group, "hooks": handlers})
    if not uninstall:
        retained.append({"matcher": MATCHER,
                         "hooks": [{"type": "command", "command": command(root), "timeout": 5}]})
    if retained:
        hooks[EVENT] = retained
    else:
        hooks.pop(EVENT, None)
    if not hooks and data.get("hooks") != {}:
        result.pop("hooks")  # do not leave an empty object behind
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    path = args.root.resolve() / TARGET
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected an object")
        updated = merge(data, args.root.resolve(), args.uninstall)
        if updated == data:
            print("Rule gate settings already current.")
            return 0
        if not args.dry_run:
            write_atomic(path, updated)
        print(f"{'Would update' if args.dry_run else 'Updated'} {path}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Settings not installed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

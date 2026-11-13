#!/usr/bin/env python3
"""Merge this clone's session-continuity hook into local host settings."""
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

EVENT = "UserPromptSubmit"
HOOK = "skills/session-continuity/engine/hook.py"
TARGETS = {"claude": ".claude/settings.local.json", "codex": ".codex/hooks.json"}


def command(root: Path, host: str) -> str:
    args = [sys.executable, str(root / HOOK), "--host", host,
            "--state-dir", str(root / "state/session-continuity")]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def is_owned(handler: dict, root: Path) -> bool:
    raw = handler.get("command", "")
    return handler.get("type") == "command" and isinstance(raw, str) and str(root / HOOK) in raw


def merge(data: dict, root: Path, host: str, uninstall: bool = False) -> dict:
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
        retained.append({"hooks": [{"type": "command", "command": command(root, host), "timeout": 10}]})
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
    parser.add_argument("--host", choices=("claude", "codex", "both"), default="both")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    hosts = tuple(TARGETS) if args.host == "both" else (args.host,)
    planned = []
    try:
        for host in hosts:
            path = root / TARGETS[host]
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError(f"{path}: expected an object")
            updated = merge(data, root, host, args.uninstall)
            if updated != data:
                planned.append((path, updated))
        # Validate both targets before writing either.
        for path, updated in planned:
            if not args.dry_run:
                write_atomic(path, updated)
            print(f"{'Would update' if args.dry_run else 'Updated'} {path}")
        if not planned:
            print("Session continuity settings already current.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Settings not installed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

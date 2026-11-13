"""Synthetic transcript stores for both harnesses. No real conversation data."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
DAY = "2026-03-04"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"session_continuity_{name}", ENGINE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ts(clock: str, day: str = DAY) -> str:
    return f"{day}T{clock}:00.000Z"


def cu(clock: str, content, **extra) -> dict:
    return {"type": "user", "timestamp": ts(clock), "message": {"role": "user", "content": content}, **extra}


def ca(clock: str, text: str, **extra) -> dict:
    return {"type": "assistant", "timestamp": ts(clock),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **extra}


def claude_file(home: Path, root: Path, sid: str, records: list[dict], slug: str | None = None) -> Path:
    folder = home / ".claude" / "projects" / (slug or "".join(c if c.isalnum() else "-" for c in str(root)))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def xu(clock: str, *texts: str) -> dict:
    return {"timestamp": ts(clock), "type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": t} for t in texts]}}


def xa(clock: str, text: str, phase: str | None = "final_answer") -> dict:
    payload = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
    if phase:
        payload["phase"] = phase
    return {"timestamp": ts(clock), "type": "response_item", "payload": payload}


def codex_file(home: Path, sid: str, cwd: Path, items: list[dict], source: object = "vscode",
               folder: str = "sessions/2026/03/04") -> Path:
    directory = home / ".codex" / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-03-04T10-00-00-{sid}.jsonl"
    meta = {"timestamp": ts("10:00"), "type": "session_meta",
            "payload": {"id": sid, "cwd": str(cwd), "source": source}}
    path.write_text("".join(json.dumps(r) + "\n" for r in [meta, *items]), encoding="utf-8")
    return path

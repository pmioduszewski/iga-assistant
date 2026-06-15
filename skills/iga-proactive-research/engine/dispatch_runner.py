#!/usr/bin/env python3
"""Dispatch runner for iga-research-dispatch (headless `claude -p`).

Reads the engine `scan --json` payload on stdin (the engine prints it to
stderr after a preamble because importing mempalace redirects sys.stdout, so
the wrapper merges 2>&1 and we extract from the first '{'), then runs each
governor-approved WORKER_REQUEST through headless `claude -p`.

Usage (called by the iga-research-dispatch zsh wrapper):
    print -r -- "$SCAN" | python dispatch_runner.py \
        <worker_prompt_path> <claude_bin> <max_dispatch> <log_path> <dry:0|1>

The engine (atomic ledger claim + governor gate) already capped and deduped
what landed in `queue`; <max_dispatch> is a belt-and-suspenders per-run cap
because a headless run cannot see the interactive Claude.ai usage line.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

# Minimal, research-appropriate tool surface. Drawer filing is via the
# IgaMemory MCP; web tools for the research itself; Read for local context.
# Deliberately NO Bash/Write — research must not mutate the working tree.
ALLOWED_TOOLS = ",".join([
    "mcp__IgaMemory__mempalace_add_drawer",
    "mcp__IgaMemory__mempalace_search",
    "mcp__IgaMemory__mempalace_check_duplicate",
    "mcp__IgaMemory__mempalace_get_drawer",
    "WebSearch",
    "WebFetch",
    "Read",
])

PER_TOPIC_TIMEOUT_S = 2400  # 40 min hard ceiling per topic (job wall_min is 30)


def _extract_json(raw: str) -> dict:
    i = raw.find("{")
    if i < 0:
        raise ValueError("no JSON object in scan output")
    return json.loads(raw[i:])


def main() -> int:
    worker_prompt_path = sys.argv[1]
    claude_bin = sys.argv[2]
    max_dispatch = int(sys.argv[3])
    log_path = sys.argv[4]
    dry = sys.argv[5] == "1"

    scan = _extract_json(sys.stdin.read())
    queue = scan.get("queue", [])
    batch = queue[:max_dispatch]
    tmpl = open(worker_prompt_path).read()
    home = os.path.expanduser("~/Iga")

    # COST SAFETY: autonomous runs default to Sonnet, NOT the job's budget.model
    # (which is Opus deep). A scheduled daily dispatcher running Opus deep
    # research (~300k tok/topic × cap) is exactly the quota burn this whole
    # out-of-session move exists to avoid. Opus is opt-in via IGA_RESEARCH_MODEL.
    model_override = os.environ.get("IGA_RESEARCH_MODEL")
    DEFAULT_SAFE_MODEL = "claude-sonnet-4-6"

    results = []
    for entry in batch:
        model = model_override or DEFAULT_SAFE_MODEL
        prompt = (
            tmpl
            + "\n\n## WORKER_REQUEST (your job context — treat as the stdin JSON)\n"
            + "```json\n" + json.dumps(entry, indent=2) + "\n```\n"
        )
        if dry:
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "model": model,
                "would_dispatch": True,
            })
            continue
        # Prompt goes via STDIN, NOT as a positional arg: `--allowedTools` is a
        # variadic flag and will swallow a trailing positional prompt
        # (verified 2026-06-15). --max-turns is generous because deep research
        # makes many tool calls AND the IgaMemory MCP needs connect time on the
        # first call.
        argv = [
            claude_bin, "-p",
            "--model", model,
            "--permission-mode", "acceptEdits",
            "--max-turns", "80",
            "--allowedTools", ALLOWED_TOOLS,
            "--add-dir", home,
        ]
        try:
            proc = subprocess.run(
                argv, input=prompt, capture_output=True, text=True,
                timeout=PER_TOPIC_TIMEOUT_S, cwd=home,
            )
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "model": model,
                "exit": proc.returncode,
                "stdout_tail": proc.stdout[-800:],
                "stderr_tail": proc.stderr[-400:],
            })
        except Exception as ex:  # noqa: BLE001 — one topic failing must not abort the rest
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "model": model,
                "error": repr(ex),
            })

    out = {
        "ts": datetime.datetime.now().isoformat(),
        "dry_run": dry,
        "queue_total": len(queue),
        "cap": max_dispatch,
        "dispatched": len(batch),
        "results": results,
    }
    with open(log_path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

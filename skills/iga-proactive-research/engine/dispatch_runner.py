#!/usr/bin/env python3
"""Dispatch runner for iga-research-dispatch (headless agent via iga_llm).

Reads the engine `scan --json` payload on stdin (the engine prints it to
stderr after a preamble because importing mempalace redirects sys.stdout, so
the wrapper merges 2>&1 and we extract from the first '{'), then runs each
governor-approved WORKER_REQUEST through `iga_llm.run_agent` (backend chosen by
IGA_PROVIDER; default claude-cli, i.e. headless `claude -p`).

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
import sys
from pathlib import Path

# Repo root on sys.path so the shared provider entry point resolves when this
# file is run as a script by the zsh wrapper.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from iga_llm import AGENT_PROVIDERS, LLMError, resolve, run_agent  # noqa: E402

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
    home = os.environ.get("IGA_HOME") or os.path.expanduser("~/Iga")
    env = dict(os.environ)
    env.setdefault("IGA_CLAUDE_BIN", claude_bin)

    # COST SAFETY: autonomous runs use the CHEAP tier, NOT the job's
    # budget.model (the smart tier). A scheduled daily dispatcher running deep
    # research on the smart tier (~300k tok/topic × cap) is exactly the quota
    # burn this whole out-of-session move exists to avoid. A bigger model is
    # opt-in via IGA_RESEARCH_MODEL.
    try:
        provider, model = resolve(
            "cheap", model=os.environ.get("IGA_RESEARCH_MODEL"), env=env
        )
        if provider not in AGENT_PROVIDERS:
            raise LLMError(f"provider {provider!r} cannot run agents; use one of {AGENT_PROVIDERS}")
    except LLMError as ex:
        out = {"ts": datetime.datetime.now().isoformat(), "fatal": str(ex), "queue_total": len(queue)}
        with open(log_path, "w") as fh:
            json.dump(out, fh, indent=2)
        print(json.dumps(out, indent=2))
        return 92

    results = []
    for entry in batch:
        prompt = (
            tmpl
            + "\n\n## WORKER_REQUEST (your job context — treat as the stdin JSON)\n"
            + "```json\n" + json.dumps(entry, indent=2) + "\n```\n"
        )
        if dry:
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "provider": provider,
                "model": model,
                "would_dispatch": True,
            })
            continue
        # --max-turns is generous because deep research makes many tool calls
        # AND the IgaMemory MCP needs connect time on the first call. Backends
        # without an allowed-tools/max-turns concept ignore both.
        try:
            res = run_agent(
                prompt, model=model, provider=provider, cwd=home, add_dirs=[home],
                allowed_tools=ALLOWED_TOOLS, max_turns=80, read_only=True,
                timeout=PER_TOPIC_TIMEOUT_S, env=env,
            )
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "provider": provider,
                "model": model,
                "exit": res.exit,
                "stdout_tail": res.stdout[-800:],
                "stderr_tail": res.stderr[-400:],
            })
        except Exception as ex:  # noqa: BLE001 — one topic failing must not abort the rest
            results.append({
                "job_id": entry.get("job_id"),
                "idempotency_key": entry.get("idempotency_key"),
                "provider": provider,
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

# iga-proactive-research

Iga skill that pre-researches upcoming meetings and tagged tasks in the background, then surfaces the findings during `/gm` and `/back`.

See `SKILL.md` for the LLM-facing spec.

## Layout

```
skills/iga-proactive-research/
  SKILL.md              ← spec (capability description, triggers, prereqs)
  engine/
    scanner.py          ← Layer 1: detection + dedup + queue writer
    worker.prompt.md    ← Layer 2: single-shot worker system prompt
  docs/
    setup-todoist-token.md
  tests/
    test_scanner.py     ← unit tests for the scanner
    conftest.py
  README.md             ← this file
```

## `engine/scanner.py`

Layer 1 of the proactive-research feature (spec: `SKILL.md`). Detects research
candidates from Todoist (`iga-research` label) and MemPalace `research-queue`
drawers, dedupes against existing research drawers, writes a work queue, and
either emits `WORKER_REQUEST` JSON for the calling Claude Code session to
dispatch (**inline mode, default**) or directly spawns `claude --bare`
subprocesses (**daemon mode**).

**Calendar was dropped in v2** (2026-05-14). Rationale: Todoist due dates
already carry the temporal signal and `iga-research` labels are cheap. May
be revisited in a future version.

### Run modes

| Mode | When | Behavior |
| --- | --- | --- |
| `inline` (default) | Fired from `/gm` and `/back` inside an interactive Claude Code session. | Scanner does detection + dedup + queue write, then prints a JSON array of `WORKER_REQUEST` objects (one per candidate, capped) to stdout. The calling session reads stdout and dispatches `Agent` tool calls with `run_in_background: true`. **No subprocesses are spawned.** |
| `daemon` | Fired by launchd/systemd outside an interactive session. | Same detection pipeline, then sequentially spawns `claude --bare -p <prompt> --session-id iga-research-<hash>` subprocesses (hard cap 3, candidate JSON delivered on stdin). **Not yet wired by an installer** — Phase 2 work. |

Switch via `IGA_RUN_MODE=inline|daemon`.

`WORKER_REQUEST` schema (one object per candidate emitted in inline mode):

```json
{
  "topic_hash": "abc123...",
  "title": "Demo with Acme",
  "context": "...",
  "target_date": "2026-05-20",
  "depth": "shallow",
  "source": "todoist",
  "source_id": "T123",
  "worker_prompt_path": "~/Gaia/skills/iga-proactive-research/engine/worker.prompt.md"
}
```

### Local run

```bash
# Smoke test — detection only, emits an empty WORKER_REQUEST list.
IGA_PROACTIVE_SPAWN=0 \
TODOIST_API_TOKEN=$(cat ~/.config/todoist/token) \
python3 ~/Gaia/skills/iga-proactive-research/engine/scanner.py
```

### Environment

| Var | Required | Meaning |
| --- | --- | --- |
| `TODOIST_API_TOKEN` | yes (or fallback file) | Todoist REST v2 token. Fallback: contents of `~/.config/todoist/token` (single line). |
| `IGA_RUN_MODE` | no | `inline` (default) or `daemon`. Invalid values exit 4. |
| `IGA_MAX_SPAWN_PER_TICK` | no | Override the per-tick cap (default 3). |
| `IGA_PROACTIVE_RESEARCH=0` | no | Full killswitch — scanner exits 0 immediately, no queue file written. |
| `IGA_PROACTIVE_SPAWN=0` | no | Detect + write queue, emit no `WORKER_REQUEST`s (inline) / spawn no subprocesses (daemon). |
| `IGA_RESEARCH_DRY_RUN=1` | no | Alias for `IGA_PROACTIVE_SPAWN=0`. |
| `IGA_RESEARCH_QUEUE_PATH` | no | Override the queue file path. Default: `~/Gaia/scratch/iga-research-queue.json`. |
| `IGA_LOG_LEVEL` | no | Python logging level. Default `INFO`. |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success (or killswitch active). |
| 1 | Config error (missing Todoist token, missing worker prompt in daemon mode). |
| 2 | MemPalace error (list/add/update failed). |
| 3 | Todoist API error. |
| 4 | Invalid `IGA_RUN_MODE`. |

### Queue file schema

`~/Gaia/scratch/iga-research-queue.json` is an array of entries:

```json
{
  "topic_hash": "...",
  "source": "todoist | mempalace",
  "source_id": "...",
  "title": "...",
  "context": "...",
  "target_date": "YYYY-MM-DD",
  "depth": "shallow | deep",
  "spawned_at": null,
  "completed_at": null
}
```

Candidates beyond the per-tick cap stay in the queue with `spawned_at: null`
and are picked up on the next `/gm` or `/back`.

### Phase 2 (not yet installed)

A future `gaia install proactive-research` skill will write a launchd plist
(Mac) or systemd timer (Linux) that fires the scanner in `daemon` mode at
fixed times even when Claude Code is closed. Not wired yet.

### Known gaps

- **`last_updated` dedup gap.** If a MemPalace drawer lacks
  `last_updated`/`created_at` in metadata, the scanner falls back to
  name-only dedup (i.e., once a `RESEARCH:<hash>` drawer exists it will
  never re-research that topic until the drawer is deleted). This is
  conservative on purpose; see `is_duplicate` in the scanner.

### Tests

```bash
cd ~/Gaia/skills/iga-proactive-research
python3 -m pytest tests/ -v
```

## `engine/worker.prompt.md`

The system prompt fed into each `claude --bare` worker spawned by the
scanner. Output contract and capability guardrails live there — keep that
file as the canonical source if you tune worker behavior.

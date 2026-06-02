---
name: mempalace-seed
description: Distill a dirty MemPalace into a validated, de-contradicted golden seed via a read-only 3-round pipeline (R1 distill + R2/R3 adversarial fresh-subagent validation). Output feeds a clean re-onboard.
status: building
prerequisites:
  - name: mempalace-importable
    description: the mempalace library must be importable from the project venv and MEMPALACE_PALACE_PATH set
    check: file(mempalace/.venv/bin/python)
    severity: error
---

## Read-only guarantee

The pipeline **never mutates the live palace**. Two defences work together:

1. `get_collection(..., create=False)` — Chroma never creates or modifies a collection.
2. `ReadOnly` wrapper — every attribute access on the collection is intercepted; any write method (`add`, `update`, `delete`, `upsert`) raises `WriteAttemptError` immediately.

Only `.get()` is permitted. The live palace is treated as immutable input.

## Pipeline flow

```
dump_material (read-only)
    └─ load_live_drawers  →  select_curated  →  material.json
          ↓
    R1 (primary agent)
        Distil raw drawers into a structured seed draft.
        Prompt: engine/prompts/r1_distill.md
        Output: seed.v1.json
          ↓
    R2 (fresh subagent — no session context from R1)
        Completeness + contradiction check against raw material.
        Prompt: engine/prompts/r2_validate.md
        Output: seed.v2.json, r2-report.json
          ↓
    R3 (fresh subagent — no session context from R1/R2)
        Correctness + factual consistency check.
        Prompt: engine/prompts/r3_signoff.md
        Output: seed.final.json, r3-signoff.md
```

Each subagent in R2/R3 spawns with a fresh context window to avoid anchoring on R1's output.

## Artifacts are personal — never commit them

Engine code (`engine/`, `tests/`, `SKILL.md`) is generic and OSS-clean. Seed output is derived from the user's personal palace and must stay private.

Write all run artifacts under a gitignored location:

```
state/golden-seed/runs/<timestamp>/
    material.json      ← curated raw input (personal)
    seed.v1.json       ← R1 output (personal)
    seed.v2.json       ← R2 output (personal)
    r2-report.json     ← R2 validation report (personal)
    seed.final.json    ← final validated golden seed (personal)
    r3-signoff.md      ← R3 sign-off narrative (personal)
```

`state/` is gitignored. Never pass a path inside the repo root as `out_path` to `dump_material`.

## Prompts

Round prompts live in `engine/prompts/`:

- `r1_distill.md` — instructions for the primary distillation agent
- `r2_validate.md` — completeness + contradiction checklist for the R2 subagent
- `r3_signoff.md` — correctness + consistency checklist for the R3 subagent

Prompts reference placeholder variables (e.g. `{material_path}`, `{seed_draft_path}`) injected by the pipeline controller.

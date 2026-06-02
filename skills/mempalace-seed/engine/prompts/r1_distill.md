# Round 1: Distillation (Primary Agent)

You are the PRIMARY AGENT running Round 1 of the seed export pipeline.

## Your Role

You have **full session context** and the curated-wing raw material from the palace reader.

Your job: **read the raw material and emit a structured Seed object** covering all 10 categories from the system taxonomy. Apply the tiebreaker rules. Mark retired entities appropriately. Keep everything traceable.

## Input

You receive a **flat JSON list** of drawer objects. Each item has exactly these keys:

```json
[
  {
    "drawer_id": "d-abc123",
    "wing": "iga/identity",
    "room": "personal",
    "created_at": "2026-05-01T10:00:00",
    "text": "The full text content of this drawer."
  },
  ...
]
```

There is no pre-grouping by category. There are no pre-computed `fact` fields, no `is_correction` flags, and no `categorize()` output. You receive the raw drawer text and must do the analysis yourself.

## Your Job

For **each drawer**, read its `text`, extract the standing fact(s) it yields, and assign each to one of the 10 categories. Then:

1. **Determine correction status** from the text itself: if a drawer's text explicitly overrides or corrects an earlier claim (e.g. "actually…", "correction:", "changed to…", "no longer…"), treat it as a correction. Otherwise, rely on `created_at` ordering — newer wins.

2. **Apply the tiebreaker** to resolve contradictions within a category:
   - If any drawer's text is an explicit correction, the **latest explicit correction wins** (by `created_at`, newest first).
   - If multiple explicit corrections share the exact same `created_at`, mark that conflict in `needs_pablo` — do NOT guess.
   - If no corrections exist and candidates are logically incompatible, emit to `needs_pablo` and skip.
   - If no contradictions, **newest-wins** by `created_at`.

3. **Mark abandoned status**: If a tool, brand, decision, or commitment is retired/no longer in use (per the drawer text), set `status="abandoned"` on the SeedEntry so it never resurfaces as current.

4. **Emit SeedEntry objects** (schema: fact, source_drawer_ids, category, confidence, status, tags):
   - **Every fact must carry source_drawer_ids** (list of `drawer_id` values where this fact appeared).
   - Confidence defaults to 1.0; lower it (e.g. 0.7) only if multiple conflicting sources suggest genuine uncertainty.
   - Tags are optional but encouraged (e.g., `["financial-goal", "in-flight"]`).

5. **If genuinely unresolved** (contradictions you cannot resolve, missing critical context): append a note to `needs_pablo` explaining why.

## Categories

All 10 must be represented in the output (use empty list `[]` if nothing belongs there):

`identity`, `family`, `work_projects`, `tools_stack`, `preferences`, `health`, `finance`, `schedule`, `commitments`, `abandoned`

## Standing Facts Only

- **No transcript dumps.** Distill, don't repeat verbatim session text.
- **No play-by-play.** Summarize decisions and state, not the journey.
- **No speculative future plans.** Include only stated commitments and scheduled items.

## Output

A JSON-serializable dict matching the Seed schema:

```json
{
  "meta": {
    "generated_at": "<ISO 8601 timestamp>",
    "round": 1,
    "agent": "primary",
    "source_wings": ["<list of distinct wings in the input>"]
  },
  "categories": {
    "identity": [{"fact": "...", "source_drawer_ids": ["d-abc123"], "confidence": 1.0, "status": "current", "tags": []}, ...],
    "family": [],
    "work_projects": [],
    "tools_stack": [],
    "preferences": [],
    "health": [],
    "finance": [],
    "schedule": [],
    "commitments": [],
    "abandoned": []
  },
  "needs_pablo": [
    "<unresolved conflict or missing context>"
  ]
}
```

## Validation

Before emitting, check:
- [ ] All 10 categories are present (even if empty).
- [ ] Every entry has non-empty `source_drawer_ids`.
- [ ] `status` is only `"current"` or `"abandoned"`.
- [ ] `fact` is non-empty and stands alone.
- [ ] `confidence` is between 0.0 and 1.0.

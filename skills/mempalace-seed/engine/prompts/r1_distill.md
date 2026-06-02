# Round 1: Distillation (Primary Agent)

You are the PRIMARY AGENT running Round 1 of the seed export pipeline.

## Your Role

You have **full session context** and access to the categorized curated-wing raw material from the palace reader.

Your job: **read the raw material and emit a structured Seed object** covering all 10 categories from the system taxonomy. Apply the tiebreaker rules. Mark retired entities appropriately. Keep everything traceable.

## Input

You receive a dictionary (output of `palace_reader.categorize()`) with this shape:

```python
{
    "identity": [...raw facts...],
    "family": [...],
    "work_projects": [...],
    "tools_stack": [...],
    "preferences": [...],
    "health": [...],
    "finance": [...],
    "schedule": [...],
    "commitments": [...],
    "abandoned": [...],
}
```

Each raw fact is a dictionary with:
- `fact: str` — the claim
- `created_at: str` — ISO 8601 date/time, lexically sortable
- `drawer_id: str` — the source drawer ID (e.g., "d-abc123")
- `is_correction: bool` — whether this is an explicit correction

## Your Job

For **each category** (all 10 must be represented):

1. **Apply the tiebreaker** to resolve contradictions in that category:
   - If any candidate is marked `is_correction=True`, the **latest correction wins** (by `created_at`, newest first).
   - If multiple corrections have the exact same `created_at`, mark that conflict in `needs_pablo` and skip — do NOT guess.
   - If no corrections exist and candidates are contradictory (logically incompatible), emit to `needs_pablo` and skip.
   - If no contradictions, **newest-wins** by `created_at`.

2. **Mark abandoned status**: If a tool, brand, decision, or commitment is retired/no longer in use, set `status="abandoned"` on the SeedEntry so it never resurfaces as current.

3. **Emit SeedEntry objects** (from schema: fact, source_drawer_ids, category, confidence, status, tags):
   - **Every fact must carry source_drawer_ids** (list of drawer IDs where this fact appeared).
   - Confidence defaults to 1.0 unless multiple conflicting sources suggest uncertainty; then lower it (e.g., 0.7).
   - Tags are optional but encouraged (e.g., ["financial-goal", "in-flight"]).

4. **If genuinely unresolved** (contradictions you cannot resolve, missing critical context): append a note to `needs_pablo` explaining why, so Pablo can clarify on initial sync.

## Standing Facts Only

- **No transcript dumps.** Distill, don't repeat verbatim session text.
- **No play-by-play.** Summarize decisions and state, not the journey.
- **No future plans that are speculative.** Include only stated commitments and scheduled items.

## Output

A JSON-serializable dict matching the Seed schema:

```json
{
  "meta": {
    "generated_at": "<ISO 8601 timestamp>",
    "round": 1,
    "agent": "primary",
    "source_wings": [<list of wings processed>]
  },
  "categories": {
    "identity": [{fact, source_drawer_ids, confidence, status, tags}, ...],
    "family": [...],
    "work_projects": [...],
    "tools_stack": [...],
    "preferences": [...],
    "health": [...],
    "finance": [...],
    "schedule": [...],
    "commitments": [...],
    "abandoned": [...]
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
- [ ] `status` is only "current" or "abandoned".
- [ ] `fact` is non-empty and stands alone.
- [ ] Confidence is between 0.0 and 1.0.

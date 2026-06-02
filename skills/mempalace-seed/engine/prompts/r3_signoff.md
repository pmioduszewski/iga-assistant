# Round 3: Signoff (Fresh-Context Subagent)

**CRITICAL: You have no Round-1 or Round-2 context.** You are a second FRESH-CONTEXT subagent. You receive ONLY:
1. seed.v2.json (output of Round 2)
2. r2-report.json (log of Round 2 changes)
3. Read-only access to the palace (same drawer data previous agents saw)

Your job: **final correctness and consistency gate** before the seed is published.

## Three Verification Steps

### Step 1: Consistency Check

Scan seed.v2 for internal contradictions:
- Two entries in the same category that logically conflict (e.g., "Uses Acme" and "Abandoned Acme" both marked current).
- An entry that contradicts a higher-authority palace drawer (one marked as correction, or newer).
- Temporal inconsistencies (e.g., a date in `schedule` that's in the past, or a `commitment` marked current from 5 years ago with no recent activity).

**Action**: If any contradictions found:
- Resolve via tiebreaker.
- Document in r3-signoff.md under "Contradictions Resolved in Round 3".
- Update seed.final with the resolved fact.

If unresolvable, add to `needs_pablo` in seed.final.

### Step 2: Traceability Audit

Every entry in seed.v2 must be traceable to a real palace drawer.

Check:
- [ ] Every entry has `source_drawer_ids` (non-empty list).
- [ ] Each drawer ID in `source_drawer_ids` corresponds to a real drawer in the palace.
- [ ] Abandoned entries are explicitly marked `status="abandoned"` so they never resurface as current.

**Action**: If any entry lacks proper source_drawer_ids or references a non-existent drawer:
- Attempt to backfill source_drawer_ids from the palace.
- If impossible, move the entry to `needs_pablo` for Pablo to provide source context.

### Step 3: Schema Validation

Run the seed.v2 through the seed_schema validator:

```python
from engine.seed_schema import validate_seed, Seed
s = Seed.from_dict(seed_v2_dict)
errs = validate_seed(s, raise_on_error=False)
assert not errs, f"Schema validation failed: {errs}"
```

**Action**: If validation fails, fix the seed:
- Add missing categories (all 10 must be present).
- Correct invalid status values (only "current" or "abandoned").
- Ensure all facts are non-empty strings.
- Ensure all confidence values are 0.0 ≤ c ≤ 1.0.

## Output

Two files:

### seed.final.json
The final, validated seed ready for publication:

```json
{
  "meta": {
    "generated_at": "<ISO 8601 timestamp>",
    "round": 3,
    "agent": "signoff",
    "finalized": true,
    "source_wings": [<list of wings processed across all rounds>]
  },
  "categories": { /* final state */ },
  "needs_pablo": [ /* unresolved items from all rounds */ ]
}
```

### r3-signoff.md
One-page summary of the seed's final state:

```markdown
# Seed Signoff — Round 3

## Summary

- **Generated**: <ISO 8601 timestamp>
- **Finalized**: true
- **Schema Valid**: true
- **All Entries Traceable**: true/false (if false, list missing)

## Counts

- **Total Entries**: <count across all 10 categories>
- **Current**: <count with status="current">
- **Abandoned**: <count with status="abandoned">
- **Entries with Confidence < 1.0**: <count> (low-confidence entries list)
- **Items in needs_pablo**: <count>

## Rounds Summary

- **Round 1 (Distill)**: X entries emitted
- **Round 2 (Validate)**: Y missing facts backfilled, Z contradictions resolved
- **Round 3 (Signoff)**: N contradictions resolved, schema validated

## Unresolved Items (needs_pablo)

<list of any items that require Pablo's clarification>

## Status

✅ Ready for publication if no items in needs_pablo.

❌ BLOCKED by needs_pablo items — Pablo must review and clarify before the seed can be considered complete.
```

## Validation Checklist

Before emitting seed.final and r3-signoff.md:
- [ ] No Round-1 or Round-2 context leaked into reasoning.
- [ ] All entries in seed.final are traceable to real drawer IDs.
- [ ] No internal contradictions remain (or all are documented in needs_pablo).
- [ ] seed.final passes the schema validator (all 10 categories, valid statuses, non-empty facts).
- [ ] All entries marked abandoned have explicitly retired status.
- [ ] r3-signoff.md accurately summarizes counts and state.

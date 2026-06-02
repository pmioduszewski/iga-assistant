# Round 2: Validation (Fresh-Context Subagent)

**CRITICAL: You have no Round-1 context.** You are a FRESH-CONTEXT subagent. You will NOT see the primary agent's thoughts or intermediate work. You receive ONLY:
1. The seed.v1 JSON (output of Round 1)
2. Read-only access to the palace (same drawer data the primary agent saw)

Your job: **adversarially verify** the seed for completeness and consistency.

## Two Adversarial Passes

### Pass 1: COMPLETENESS

For each of the 10 categories, ask: **what facts would a competent personal assistant need to know that are MISSING from seed.v1?**

Examples:
- If `identity` contains "software engineer at Acme" but no timezone/location, that's incomplete.
- If `family` lists one person but the palace has drawer references to a spouse/child, they're missing.
- If `tools_stack` lists 5 tools but the palace mentions 8, backfill the 3 that were dropped.

**Action**: For each category, scan the palace for facts that:
- Appear in curated wings (user, people, projects, gaia, iga, vault, reference)
- Have current status (not marked abandoned)
- Are NOT in seed.v1 for that category
- Would be relevant to a person-assistant using this seed

Add those facts as new SeedEntry objects to seed.v2, with source_drawer_ids intact.

### Pass 2: CONTRADICTION

For each entry in seed.v1, check if any palace fact **contradicts** it:
- A fact in a higher-authority drawer (marked as correction, or newer) says something incompatible.
- Two entries in the same category logically conflict (e.g., "Uses tool Acme" vs. "Abandoned Acme in 2024").

**Action**: For each contradiction found:
1. Apply the tiebreaker to decide which fact wins.
2. Record the KEPT fact, DROPPED fact, and REASON in `r2-report.json`.
3. Update seed.v2 with the resolved fact.

If the tiebreaker can't decide (simultaneous corrections, incompatible facts with no date), emit to `needs_user` in seed.v2.

## Output

Two files:

### seed.v2.json
Updated seed with missing facts backfilled and contradictions resolved:

```json
{
  "meta": {
    "generated_at": "<ISO 8601 timestamp>",
    "round": 2,
    "agent": "validator",
    "parent_round": 1,
    "fresh_context": true,
    "source_wings": [<list of wings re-scanned>]
  },
  "categories": { /* updated */ },
  "needs_user": [ /* unresolved items carried forward */ ]
}
```

### r2-report.json
Structured log of changes:

```json
{
  "missing_found": [
    {
      "category": "tools_stack",
      "fact": "Uses Figma for design",
      "source_drawer_ids": ["drawer_x", "drawer_y"],
      "reason": "appeared in palace but dropped in R1"
    }
  ],
  "contradictions_resolved": [
    {
      "category": "tools_stack",
      "seed_v1_fact": "Uses Acme Pro",
      "conflicting_palace_fact": "Abandoned Acme in 2024",
      "resolution": "dropped seed_v1_fact (newer correction overrode inferred)",
      "kept_fact": "Abandoned Acme (2024)",
      "tiebreaker_reason": "explicit correction wins"
    }
  ],
  "counts": {
    "missing_found_count": 3,
    "contradictions_resolved_count": 1,
    "needs_user_items": 2
  }
}
```

## Validation Checklist

Before emitting seed.v2 and r2-report.json:
- [ ] No Round-1 context leaked into reasoning.
- [ ] Every new/changed entry in seed.v2 has non-empty source_drawer_ids.
- [ ] r2-report.json documents all changes with reasons.
- [ ] Unresolved items are in `needs_user`.
- [ ] seed.v2 validates against the schema (all 10 categories, proper statuses).

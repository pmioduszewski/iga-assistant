# Supersedence edges in MemPalace — design proposal

> **STATUS: PROPOSED — review before implementing** (2026-05-15)
>
> Author: Iga (drafted on the user's behalf). Read carefully and resolve the
> Open Questions section before any code lands.

## Problem statement

Iga keeps surfacing stale facts at decision time. The canonical bug:
during the daily brief, Iga flags an "<old-domain> domain renewal" email as
business-critical despite the user having rebranded **<old-brand> → <new-brand>**
months ago. The rebrand drawer *is* in the palace; vector similarity just
doesn't retrieve it alongside the email — they look like competing
results, not connected ones, so the brief never sees them in the same
window.

Generalize: any time a user's reality changes (project rename, recipe
revision, decision reversal, "we don't use X anymore"), the *new* truth
gets filed, but the *old* truth keeps winning recall lotteries because
each chunk is scored independently. The retrieval surface treats time and
correction as out-of-band metadata; downstream LLMs are forced to
re-derive currency from clues in the chunk text.

The proposal: make **supersedence** a first-class structural relationship
in the palace, so retrieval and invalidation can carry currency forward
without every consumer re-inventing it.

## Current state (what exists today)

### Drawer schema

Drawers are ChromaDB documents in the `mempalace_drawers` collection.
The metadata dict, written at `add_drawer` time (mcp_server.py:838),
contains:

```
wing            str   normalized wing slug (e.g. "gaia")
room            str   room slug within the wing
source_file     str   provenance path (may be "")
chunk_index     int   0 for MCP-added drawers; >0 for chunked mines
added_by        str   "mcp" / "miner" / etc.
filed_at        str   ISO-8601 timestamp
```

Plus, for miner-filed drawers, `normalize_version`, `source_mtime`, and
adapter-specific keys (not relevant here).

**Drawer ID** is deterministic:
`drawer_{wing}_{room}_{sha256(wing+room+content)[:24]}`.

There is **no `status`**, no `superseded_*`, no validity timestamps on
drawers themselves. Currency lives only in the prose of the drawer.

### Tunnel schema (the existing primitive we'll ride on)

Tunnels live in `~/.mempalace/tunnels.json` — a flat JSON list outside
ChromaDB, mode 0600 in a 0700 parent. Each tunnel:

```json
{
  "id": "<16-hex>",            // sha256 of sorted("wing/room" ↔ "wing/room")
  "source": {"wing": "...", "room": "...", "drawer_id": "..."?},
  "target": {"wing": "...", "room": "...", "drawer_id": "..."?},
  "label": "free text",
  "kind": "explicit" | "topic",
  "created_at": "<iso8601>",
  "updated_at": "<iso8601>"?
}
```

Key properties of the existing tunnel layer:

- **Undirected.** `create_tunnel(A, B)` and `create_tunnel(B, A)` hash to
  the same ID. The dict preserves caller-given direction for display, but
  the canonical ID is symmetric. **This is the single biggest design
  constraint for supersedence — supersedence is directed; the tunnel
  primitive is not.** (See open question 1.)
- **Per-tunnel `kind`** discriminator already exists with values
  `"explicit"` and `"topic"`. Adding a new kind is the lowest-friction
  extension point.
- **Endpoint granularity is wing/room, not drawer.** `drawer_id` on
  source/target is optional and informational; it isn't used as the
  primary key, and `follow_tunnels` returns it only as a hydration hint.
  Supersedence is fundamentally a *drawer-to-drawer* relation. (Open
  question 2.)
- `create_tunnel` does a symmetric-ID dedup and updates label on a second
  call with the same endpoints — fine for our needs as long as we accept
  the undirected-ID semantics.
- Writes are guarded by `mine_lock(_TUNNEL_FILE)`. Concurrent supersedence
  edges from parallel workers serialize correctly.

### `mempalace_search` behavior

Path: `mcp_server.tool_search` → `searcher.search_memories`.

1. Query the drawers collection for `3 × n_results` candidates with
   optional `where={"wing": ..., "room": ...}` filter and a distance cap.
2. Query the closets collection (the BM25-friendly topic index) and
   compute a per-source-file rank-based **boost** (`-0.40` distance for
   the top closet hit, decaying to `-0.04` for the fifth).
3. Subtract the boost from the cosine distance, sort, take top-N.
4. For closet-boosted hits with multiple drawers in the same source,
   keyword-rerank within the source and expand the winning chunk with one
   neighbor on each side (this is the "drawer-grep enrichment" step).
5. BM25-hybrid re-rank the final candidate set.
6. Return a result dict whose `results` is a list of:

   ```
   {text, wing, room, source_file, created_at, similarity, distance,
    effective_distance, closet_boost, matched_via, [closet_preview,
    drawer_index, total_drawers]}
   ```

There is currently **no field** in the result row that reflects
correction status, validity, or supersedence. Returned hits are
identified by `text + wing + room + source_file` — **the drawer ID is
not surfaced to the caller**. That's a blocker for letting downstream
LLMs reason about supersedence and is the second-biggest constraint
(see open question 3).

### `mempalace_kg_invalidate` behavior

Path: `mcp_server.tool_kg_invalidate` → `KnowledgeGraph.invalidate`.

Operates on the **knowledge_graph** SQLite store (`triples` table) —
a completely separate substrate from drawers and tunnels. It sets
`valid_to` on a `(subject, predicate, object)` row whose `valid_to` is
NULL. Bitemporal-style: nothing is deleted; rows accrue.

Critical: **`kg_invalidate` does not touch drawers.** A drawer about
"<old-brand> domain renewal" is invisible to the KG layer unless an adapter
extracted a triple from it (and the KG table has the
`source_drawer_id` provenance column, populated only when adapters set
it — sparse in current data per the `_migrate_schema` comments).

So today, an LLM that calls `kg_invalidate("the user", "owns_brand",
"<old-brand>")` correctly closes the KG row but leaves every <old-brand> drawer
fully retrievable with no marker.

## Proposed schema additions

### 1. New tunnel kind: `supersedes`

Reuse the existing tunnel substrate. Introduce **`kind: "supersedes"`**
with these conventions on the dict:

```json
{
  "id": "sup_<12hex>",
  "kind": "supersedes",
  "predecessor": {"wing": "...", "room": "...", "drawer_id": "<id>"},
  "successor":   {"wing": "...", "room": "...", "drawer_id": "<id>"},
  "reason": "rebrand: <old-brand> → <new-brand>",
  "created_at": "<iso>",
  "created_by": "iga" | "user" | "migration:<name>",
  "confidence": 0.0-1.0
}
```

Differences from the existing tunnel schema (rationale in open questions):

- New top-level fields `predecessor` / `successor` instead of
  `source` / `target`. Direction is part of the semantics, not a display
  hint.
- `drawer_id` is **required**, not optional. Supersedence is between
  facts; facts live in drawers.
- Canonical ID is **directed**:
  `sha256("sup||" + predecessor.drawer_id + "→" + successor.drawer_id)[:12]`,
  prefixed `sup_`. `create_tunnel(A→B)` and `create_tunnel(B→A)` produce
  *different* IDs. This requires either (a) a new write function
  `create_supersedence` that bypasses `_canonical_tunnel_id`, or
  (b) refactoring `_canonical_tunnel_id` to consult `kind`. **(b) is
  recommended** — one entry point, dispatch on kind.
- A new on-disk file `~/.mempalace/supersedence.json` mirroring tunnels.json
  is an alternative if we want to avoid mixing directed and undirected
  edges in the same JSON file. **Recommendation: keep them in
  `tunnels.json`** — one less file to lock, one less invariant to drift.
  Readers already discriminate by `kind`.

### 2. Drawer metadata additions

Add three optional metadata keys to drawers (ChromaDB allows arbitrary
flat metadata, so this is additive and non-breaking — older drawers
simply lack the keys):

```
status            "current" | "superseded"            default: omitted (== "current")
superseded_at     ISO-8601 date                       null/omitted
superseded_by_id  drawer_id of successor              null/omitted
```

These are denormalized for cheap retrieval-time filtering — the
authoritative store is the supersedes-edge in tunnels.json. Whenever
an edge is created, we **also** patch the predecessor drawer's metadata
via `col.update(ids=[pred_id], metadatas=[{...}])`. If the two ever
disagree (manual edit, crash mid-write), the edge wins on next
reconciliation pass (see Migration).

Trade-off: this is the closest the proposal gets to a "soft delete"
flag. Justified because (a) Chroma `where` filters can short-circuit
retrieval cheaply, (b) edge traversal happens lazily and we want the
common path to be index-only.

### 3. No KG schema changes (yet)

The KG already has bitemporal `valid_from` / `valid_to`. Supersedence
on KG triples is already expressible. The change to `kg_invalidate` is
**behavioral**, not schema-level (see API changes §3).

### 4. Index considerations

ChromaDB indexes flat metadata at query time via `where`. No new index
is strictly needed — `where={"status": "current"}` is a cheap equality
filter. Recommended optimization: ensure `status` is a top-level
metadata key (not nested) so Chroma's where-filter takes the fast path.

If we ever need "give me all current drawers in wing X" as a hot path,
that's a separate enhancement — current data volume (~4,350 drawers)
doesn't warrant it.

## API changes (MCP tool surface)

Strict additive policy. Every existing caller must keep working unchanged.

### `mempalace_add_drawer` — extended

New optional params:

```
supersedes_drawer_id   str | None   # if set, file the new drawer AND create
                                    # a supersedes edge from this old drawer
                                    # to the new one
supersedes_reason      str | None   # free-text reason; stored in edge.reason
```

Behavior when `supersedes_drawer_id` is set:

1. File the new drawer as today.
2. Validate that `supersedes_drawer_id` exists in the palace. On miss,
   return a soft warning (`{"success": true, "warning": "predecessor
   not found, no edge created"}`) rather than failing — the new drawer
   is still valuable.
3. Create the supersedes edge (predecessor → new drawer).
4. Patch the predecessor's metadata with `status="superseded"`,
   `superseded_at=<today>`, `superseded_by_id=<new_id>`.
5. Invalidate the graph cache.

Return shape adds `supersedence_edge_id` when one was created.

### `mempalace_search` — extended

New optional params:

```
include_superseded   bool    default: False
follow_supersedes    bool    default: True
```

Behavior:

- **Default (include_superseded=False, follow_supersedes=True):**
  Filter `status="superseded"` out of the top-level result set. For
  every drawer in the final ranked list that was *itself* a predecessor
  to some current drawer (we detect this by reverse-walking edges), the
  result entry gains a `superseded_by` sub-object pointing to the
  successor. We do **not** add the successor as a separate row — it
  was either already in the ranked set on its own merit, or it's added
  as an attachment to its predecessor.
- **`include_superseded=True`:** return superseded drawers as
  first-class hits. Useful for archaeology / "what did I used to
  think". Each carries `state: "superseded"` and the edge metadata.
- **`follow_supersedes=False`:** disable edge fan-out entirely (the
  pre-supersedence behavior, for callers that need bit-exact backward
  compat).

Every result row gains a new field `state` with values
`"current" | "superseded"`. Existing callers that don't read it are
unaffected.

**Critical implementation note:** result rows currently lack a
`drawer_id`. We must add `drawer_id` to every result row regardless of
whether supersedence is in play — without it, neither this proposal
nor any downstream LLM reasoning can identify rows. This is a small
schema change to the result, and it's safe because all current
callers consume the dict by named keys.

#### Worked example: "<old-brand> domain renewal"

Today:
1. Email triage filed a drawer in `iga/email/...` describing the
   <old-domain> renewal reminder.
2. The rebrand decision sits in
   `gaia/decisions/<id>` as a current drawer.
3. Iga's brief asks `mempalace_search("<old-brand> domain renewal")`.
4. The renewal drawer scores high; the rebrand drawer doesn't share
   enough surface tokens to clear the threshold. Brief surfaces "renew
   your <old-domain> domain" verbatim.

With supersedence:
1. When the user files the rebrand decision via `add_drawer`, he passes
   `supersedes_drawer_id=<old_<old-brand>_brand_drawer>`. Edge created.
2. Email triage files the renewal email as today — no edge.
3. Brief queries `mempalace_search("<old-brand> domain renewal")`. The
   renewal drawer scores high. The retrieval walker checks: does this
   drawer's content mention an entity (`<old-brand>`) that participates in
   any `supersedes` edge with the email's brand-noun? Yes — the brand
   itself has been superseded. The renewal result row gains
   `brand_status: "superseded"`, with the successor reference attached.
4. Brief LLM now sees "the brand on this renewal email is superseded
   by <new-brand>" inline. Question becomes "do we still want the domain
   even though we don't use the brand?" — a different and correct
   question.

**This worked example reveals a gap:** the most common case is *not*
drawer-to-drawer supersedence, it's **entity-to-entity** (`<old-brand> →
<new-brand>` as a brand name) that the email *references*. That's the
KG layer's job. The drawer-level edge handles "rev 1 of decision is
superseded by rev 2"; the KG-level invalidation handles "the entity
<old-brand> is no longer the active brand". Both are needed; see
"Retrieval behavior" below for how they cooperate.

### `mempalace_kg_invalidate` — behavior change

Today: sets `valid_to` on the triple, no drawer-side effect.

Proposed:

1. Set `valid_to` as today (unchanged).
2. **Additionally**, find every drawer in the palace where
   `source_drawer_id` on any KG triple equals the invalidated
   triple's `source_drawer_id`. For those drawers, leave them alone
   — they're verbatim history. **Do not** mark drawers as superseded
   based on KG invalidation; entities can be invalidated without
   their containing drawers being wrong. (E.g. "the user no longer
   works at company X" doesn't make the diary entry describing his
   resignation a "stale" drawer.)
3. **If `successor_subject`/`successor_object` params are provided**
   (new optional args), create a KG-level supersedence link by
   inserting a new triple `(old_subject, "superseded_by", new_subject,
   valid_from=today)`. This makes the KG self-describing — a query
   for the old entity now returns the successor inline.

Net effect: `kg_invalidate` keeps its existing surface (no new
required params; old call sites work) but gains the ability to record
*what replaced* the fact, not just *when it ended*. That's the
asymmetry today: we can say "fact ended on 2026-03-01" but not
"because fact-Y started".

Backward compatibility: 100% — new args are optional. Old behavior is
unchanged when they're omitted.

### Possible new tool: `mempalace_mark_superseded`

```
mempalace_mark_superseded(old_drawer_id, new_drawer_id, reason="")
```

Same effect as `add_drawer(..., supersedes_drawer_id=...)` but for
linking two *already-filed* drawers retroactively. Useful for
migration and for the LLM noticing "wait, this drawer I just filed
last week supersedes that other one from January".

Returns the edge dict.

**Recommendation: ship this tool.** Without it, the only way to
retro-link is to re-file content via `add_drawer`, which duplicates
the drawer.

### Possible new tool: `mempalace_list_supersedence`

```
mempalace_list_supersedence(drawer_id, direction="both")
```

Returns edges where `drawer_id` participates. Useful for the
retrieval walker (the consumer of `follow_supersedes=True`) and for
debug/inspection. Returns lists `superseded_by` (outgoing) and
`supersedes` (incoming).

**Recommendation: ship this too.** It's the read-side counterpart to
`mark_superseded` and `follow_tunnels` doesn't naturally express the
direction.

### Backward compatibility summary

| Surface | Change | Breaks existing callers? |
|---|---|---|
| `mempalace_add_drawer` | new optional params, return adds optional `supersedence_edge_id` | No |
| `mempalace_search` | new optional params, result rows gain `state` and `drawer_id`, may filter superseded by default | **Yes, behaviorally**: a caller relying on superseded drawers being returned will see them disappear from default search. Mitigation: feature-flag the filter behavior during phases 1–3 (see Implementation phases). |
| `mempalace_kg_invalidate` | new optional params, optional new triple insert | No (purely additive) |
| New: `mempalace_mark_superseded` | new tool | No |
| New: `mempalace_list_supersedence` | new tool | No |
| `mempalace_create_tunnel` | unchanged externally; internal `_canonical_tunnel_id` now dispatches on `kind` | No |

## Retrieval behavior changes

Restating from the API section in one place:

1. **Search baseline:** vector + closet hybrid retrieval as today,
   producing a ranked candidate set.
2. **Status filter:** if `include_superseded=False`, drop rows where
   `status == "superseded"` from the final list. Cheap metadata filter
   applied *after* ranking so the ranker isn't penalized for content
   it doesn't know about.
3. **Edge fan-out:** for each remaining row, look up its
   `superseded_by_id` (denormalized in metadata) and, if present,
   attach a `superseded_by` field with `{drawer_id, wing, room,
   preview}` — preview hydrated from a `col.get(ids=[...])` batch
   call so we do at most one extra Chroma read per search.
4. **Predecessor backfill:** for each current drawer with `_in_edges`
   (we walk the predecessor set), attach `supersedes: [...]`. This
   lets a search for the successor still report what it replaced
   ("<new-brand> rebrand, supersedes the <old-brand> brand drawer").
5. **State surfacing:** every row carries `state` so consumers can
   render it however they like (badge, log line, omission).
6. **No semantic re-ranking based on currency.** We don't down-rank
   superseded drawers in *score* — we either include them or filter
   them. (Following the "remove, don't down-rank" guidance from
   applied-llms.org.)

The retrieval changes are entirely orthogonal to the parallel
hybrid-retrieval workstream — they operate on whatever ranked set is
produced.

## Migration plan for existing palace (~4,350 drawers)

### Strategy: lazy by default, opt-in batch tool

1. **No automatic retroactive migration on upgrade.** The palace
   starts post-upgrade with zero supersedence edges. Nothing breaks.
2. **Provide a CLI tool** `mempalace supersede-scan` (engine-side,
   not MCP) that detects probable correction pairs.

### Detection heuristics for `supersede-scan`

Highest-signal sources, in order:

1. **`gaia/rules/corrections` and `gaia/rules` wing in general.**
   the user files explicit "I was wrong about X, the truth is Y" drawers
   here. Heuristic: any drawer whose text contains an explicit
   correction marker (`corrects:`, `supersedes:`, `update to:`, "no
   longer", "previously called", "renamed to") → LLM-call to extract
   `(old_drawer, new_drawer, reason)` triples → present candidate
   list to the user → he confirms each.
3. **KG triples with `valid_to` set but no `superseded_by` triple.**
   For each closed triple, look for a triple with the same subject,
   same predicate, different object, where the new triple's
   `valid_from >= old.valid_to`. That's a successor.
4. **Diary entries marked `aaak.correction` or matching the AAAK
   correction microformat.** Mine these as supersedence candidates.
5. **Brand/project rename markers** — file-level detection: search
   across the palace for `<old_name> → <new_name>` strings; for any
   pair found ≥3 times, propose a wholesale rename event whose edges
   touch every drawer containing the old name.

The tool runs in dry-run mode by default, writing a JSON candidate
report. The user (or any user) reviews; a second run with `--apply`
creates the edges.

### Reversibility

Every operation is logged to the `wal/` directory (existing
`_wal_log` infrastructure). The CLI ships with `mempalace supersede-undo
<run_id>` that walks back the edges and metadata patches from a given
scan-and-apply session. Safe to roll back.

### Drawer metadata vs edge truth — reconciliation

If a drawer's `status="superseded"` but no edge points at it
(corruption, partial write), the next read sets `status=` back to
absent. If an edge exists but the predecessor's metadata doesn't
reflect it (older edge from a crash window), the next read of either
endpoint reconciles. Reconciliation is idempotent.

## Testing strategy

### Unit tests

- `tests/test_supersedence_tunnel.py`:
  - canonical ID is directed (A→B ≠ B→A)
  - same A→B twice updates label/reason, doesn't dup
  - missing predecessor drawer ID raises
  - kind="supersedes" coexists with kind="explicit"/"topic" in same file
- `tests/test_add_drawer_supersedes.py`:
  - filing with `supersedes_drawer_id` creates the edge and patches metadata
  - filing with a non-existent predecessor returns warning, no edge
- `tests/test_search_state.py`:
  - default search filters superseded rows
  - `include_superseded=True` returns them with `state="superseded"`
  - current rows attach `supersedes`/`superseded_by` backrefs
  - `drawer_id` appears on every result row
- `tests/test_kg_invalidate_successor.py`:
  - invalidate with successor args inserts the `superseded_by` triple
  - invalidate without successor args is byte-identical to old behavior

### Integration test (the one that matters)

`tests/integration/test_brand_rebrand_suppressed.py`:

1. File drawer A: "active brand: <old-brand>. Domain: <old-domain>"
2. File drawer B: "rebrand decision: <old-brand> → <new-brand>, effective
   2026-02-01" with `supersedes_drawer_id=A_id`.
3. File drawer C: "Reminder: renew <old-domain> domain by 2026-12-15."
4. Call `mempalace_search("<old-brand> domain renewal")`.
5. Assert: C is in results. A is **not** (filtered: superseded). B is
   either in results or attached to C as a referenced supersedence
   context (via entity overlap in KG, if implemented).
6. Assert: C's row contains evidence that "<old-brand> brand is superseded".

This is the regression test the eval-harness workstream consumes as
the `brand-rebrand-suppressed` scenario.

### Migration tests

- `tests/test_supersede_scan_dry_run.py`: synthetic palace with 50
  drawers including 5 known correction pairs; assert scan finds ≥4 of
  them with confidence ≥0.7.
- `tests/test_supersede_undo.py`: apply, undo, assert palace state
  identical to pre-apply.

## Implementation phases

1. **Phase 1 — schema + new tunnel kind, no behavior change.**
   - Refactor `_canonical_tunnel_id` to dispatch on `kind`.
   - Accept `kind="supersedes"` in `create_tunnel` (or add a
     dedicated `create_supersedence` wrapper).
   - Reader code (`list_tunnels`, `follow_tunnels`) understands the
     new kind but doesn't surface it in MCP yet.
   - Drawer metadata `status`/`superseded_*` fields documented;
     `add_drawer` accepts the params but is **gated by an env var**
     `MEMPALACE_SUPERSEDENCE=1` for development.
2. **Phase 2 — new API surface, opt-in.**
   - `mempalace_mark_superseded` and `mempalace_list_supersedence`
     ship as MCP tools.
   - `mempalace_add_drawer` accepts `supersedes_drawer_id` regardless
     of env flag.
   - `mempalace_search` accepts the new params, but the *default* is
     still old behavior (`include_superseded=True`, no edge
     fan-out). Result rows gain `drawer_id` unconditionally.
3. **Phase 3 — search default change behind feature flag.**
   - Default flips to `include_superseded=False`, edge fan-out
     enabled, when env var `MEMPALACE_SUPERSEDENCE_DEFAULT=1`. The user
     runs with the flag for ≥2 weeks while we evaluate.
4. **Phase 4 — `kg_invalidate` writes successor triples.**
   - New optional params land. Old call sites unchanged.
5. **Phase 5 — retroactive migration tooling.**
   - `mempalace supersede-scan` CLI ships. Run on the user's palace,
     review results together, apply.
6. **Phase 6 — flip default, remove feature flag.**
   - After stability period, `MEMPALACE_SUPERSEDENCE_DEFAULT=1`
     becomes the only behavior. Doc the breaking change in CHANGELOG;
     bump minor version (this is a behavioral change to
     `mempalace_search` defaults, so it's not pure backward-compat).

## Open questions for the user

1. **Directed edges in `tunnels.json`?** Adding a directed kind to a
   file historically containing undirected edges is a smell. Cleaner
   alternative: a separate `~/.mempalace/supersedence.json` file with
   identical lock/atomic-write/0600 conventions. Costs: more files,
   two paths to keep in sync. Benefits: cleaner invariant ("tunnels
   are symmetric, supersedence is directed"). **Recommendation:
   one file; willing to be overruled.**

2. **Drawer vs wing/room as the supersedence endpoint.** I proposed
   drawer-level. But sometimes the supersedence is "everything in
   wing `<old-brand>_brand` is now in wing `<new-brand>_brand`" — bulk renames.
   Should we support a `wing_supersedes_wing` second edge type, or
   force everything through drawer-level edges with batching?
   **Recommendation: drawer-level only for v1; revisit if scan
   surfaces >50 cases of bulk supersedence.**

3. **Adding `drawer_id` to search result rows.** Currently absent.
   This is *strictly* additive but it's a noticeable result-shape
   change. Worth doing regardless of supersedence — third-party
   tooling has wanted it for ages. Confirm OK to land in phase 2?

4. **`include_superseded` default value.** I propose `False` (post
   phase 6). Alternative: keep default `True` forever and require
   explicit opt-in for the new behavior — safer for OSS users but
   defeats the point (every Iga-like caller has to remember to opt
   in). **Recommendation: default `False`, document the change
   prominently.**

5. **Should `kg_invalidate` cascade to drawer state?** I said no
   (drawers are verbatim history). But there's an argument that
   invalidating "<old-brand> is the brand" *should* visibly mark drawers
   that present <old-brand> as current. Pushing back: that's a query-time
   concern (the brief's filter), not a storage-time concern.
   Confirm "no cascade"?

6. **Confidence threshold for the `supersede-scan` autoapply path.**
   Heuristics 1–4 produce a confidence score. What's the cut-off
   below which we *don't* surface the candidate at all (vs surface
   for review)? Suggest 0.5.

7. **Telemetry.** Do we want `mempalace_status` to report supersedence
   edge count and stats? Recommendation: yes, in the existing
   `mempalace_status` payload, behind a top-level `supersedence:
   {edges: N, current: M, superseded: K}` section.

8. **The really hard one: entity-level supersedence vs drawer-level
   supersedence.** Drawer-level is the easier mechanism but only
   solves part of the <old-brand>-renewal case (it stops the *brand
   decision* drawer from beating the *rebrand decision* drawer; it
   does not stop a renewal email that merely *mentions* "<old-domain>"
   from looking current). The full fix is to also surface "the
   entity <old-brand> is superseded by <new-brand>" from the KG into search
   result rows whose `text` mentions <old-brand>. That's a substantially
   bigger lift — entity extraction at retrieval time. Should v1
   stop at drawer-level edges and queue the entity-overlay work as
   a phase 7, or are we biting both off?

## Out of scope (explicitly)

- Hybrid retrieval (BM25 + embeddings + reranker) — separate workstream.
- Contextual chunking — separate workstream.
- Orchestrator-workers refactor of `/back` — separate workstream that
  consumes this design's results.
- UI / visualization for the supersedence graph.
- Cross-palace federation of supersedence edges.

## Citations

- Anthropic Multi-Agent Research System
  (anthropic.com/engineering/multi-agent-research-system) — separation
  of retrieval from synthesis; agents shouldn't be re-deriving truth
  state on every call.
- applied-llms.org — factual drift; "remove, don't down-rank" the
  signal for currency.
- MemPalace research drawer: `gaia/architecture/68ecca8703c2f1078434d7ce`
  (filed 2026-05-15) — SOTA framing.
- MemPalace source paths referenced in this doc (read-only at design
  time):
  - `mempalace/.venv/.../mempalace/palace_graph.py` — tunnel layer.
  - `mempalace/.venv/.../mempalace/knowledge_graph.py` — KG layer.
  - `mempalace/.venv/.../mempalace/searcher.py` — search pipeline.
  - `mempalace/.venv/.../mempalace/mcp_server.py` — MCP tool surface.

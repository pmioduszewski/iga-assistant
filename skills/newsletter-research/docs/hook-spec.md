# Email Hook Spec — Schema Reference

An email hook is a single Markdown file (frontmatter + body) that tells the
generic `newsletter-research` engine **what to look for** in a labeled mail
stream and **where to file findings**. The engine is generic; all the
opinion lives in the hook spec.

Personal hook files live in `rules/hooks/<name>.md` (gitignored — never
committed). A PII-free example lives in
`skills/newsletter-research/examples/example-hook.md`.

---

## Frontmatter fields

```yaml
---
name: <slug>                        # Required. Machine-readable id, e.g. "dev-tools"
description: <one line>             # Required. Human summary for /iga status.

trigger:                            # Required. One of:
  gmail_label: "Newsletter/Dev"     #   a Gmail label to match, OR
  gmail_query: "label:foo from:bar" #   a raw Gmail search query

interest_profile: |                 # Required. Free-form natural language.
  What you care about — artifacts, topics, domains the worker should
  treat as "interesting". The worker uses this verbatim as its
  evaluation lens. There is no structured vocabulary; write naturally.
  Examples:
    "libraries and tools that could improve my software projects or
     CTO practice — performance, DX, observability, AI/ML, databases"
    "practical parenting tips for toddlers aged 0–3"
    "promotions on kids' clothing and toys under 150 PLN"

scoring_context:                    # Required. List of MemPalace wing/room globs
  - "projects/*"                    # the worker queries to establish relevance.
  - "family"                        # Supports glob wildcards (same as mempalace_search).
  - "user/interests"

fit_threshold: 2                    # Optional. Integer 0–3. Default: 2.
                                    # Findings scoring < threshold are dropped.

output_wing: "vault/dev-tools"      # Required. MemPalace wing where findings
                                    # are filed. Resolved deterministically:
                                    # "/" → "-" gives the wing, room is always
                                    # "findings" (vault/dev-tools →
                                    # wing vault-dev-tools, room findings).

sinks:                              # Optional. Generic delivery contract.
  - sqlite                          # Flat list of sink TYPE names (the
  - todoist                         # minimal parser can't do list-of-maps).
                                    # mempalace is ALWAYS implied + canonical
                                    # (dedup authority) — never list it.
                                    # sqlite = zero-account local floor,
                                    # always ensured even if omitted.
                                    # todoist = opt-in adapter, needs the
                                    # `todoist_project` key below.
                                    # Omit `sinks:` entirely → default
                                    # [sqlite] (+ implicit mempalace).
                                    # Future: slack, notion (same contract).

todoist_project: "Iga Research"     # Optional. Per-sink config for the
                                    # todoist sink (the parser is flat, so
                                    # config lives in dedicated keys, not
                                    # nested under sinks). If set, each
                                    # newly-filed finding becomes a Todoist
                                    # task ("Evaluate: <title>", clickable
                                    # URL, fit→priority). Personal-layer
                                    # value — lives in rules/hooks/<name>.md,
                                    # never upstream. A todoist sink with no
                                    # project is dropped (sqlite floor holds).

cadence: on-demand                  # Optional. "on-demand" (default) or "auto".
                                    # "auto" = arm via queue flag drawer;
                                    # "on-demand" = manual /research-newsletter only.

status: active                      # Optional. "active" (default) or "paused".
                                    # "paused" = engine discovers but skips.
---
```

## Body (optional)

Free-form Markdown providing additional context for the worker. May contain:

- Topic include/exclude lists
- Additional scoring guidance specific to this hook
- Notes on what NOT to capture (noise suppression)

The body is appended to the worker's system prompt as an `## Additional hook
context` section.

---

## Validation rules (enforced by `engine/hook_spec.py`)

| Field | Required | Type | Validation |
|---|---|---|---|
| `name` | Yes | string | non-empty slug (`[a-z0-9-]+`) |
| `description` | Yes | string | non-empty |
| `trigger` | Yes | dict | must have `gmail_label` OR `gmail_query` (not both) |
| `interest_profile` | Yes | string | non-empty after strip |
| `scoring_context` | Yes | list[str] | at least one entry |
| `fit_threshold` | No | int | 0–3, default 2 |
| `output_wing` | Yes | string | non-empty; `/`→`-` = wing, room always `findings` |
| `sinks` | No | list[str] | sink type names (`sqlite`/`todoist`); mempalace implied; default `[sqlite]`; unknown type → `HookSpecError` |
| `todoist_project` | No | string | per-sink config for the todoist sink (personal-layer value) |
| `cadence` | No | string | "on-demand" or "auto", default "on-demand" |
| `status` | No | string | "active" or "paused", default "active" |

A spec that fails validation raises `HookSpecError` with a descriptive
message — the engine surfaces this as a job-load error (same path as
`SchemaError` in `schema.py`), never crashes silently.

---

## Canonical flag-drawer schema (RESOLVED — read this before editing producers/triggers)

A *flag drawer* is the unit the producer files into the
`newsletter-research-queue` MemPalace room; the generic engine's
`mempalace(room:newsletter-research-queue)` trigger consumes it and the
runner processes one email per flag.

### The contract discrepancy (and how it is resolved)

STEP-1 `SKILL.md` documented filing the flag via
`mempalace_add_drawer(..., metadata={title, target_date, hook_name}, content=...)`.
**That signature does not exist.** The real MCP tool is:

```
mempalace_add_drawer(wing, room, content, source_file=None, added_by="mcp")
```

There is **no `metadata=` parameter**, and `mempalace_list_drawers` (what the
trigger reads through) returns each drawer as
`{drawer_id, wing, room, content_preview}` — **no `metadata`, no full
`content`** (the preview is the first ~200 chars of the body).

**Resolution (canonical, binding):** the structured fields are encoded as
`key: value` lines **inside `content`**. The producer
(`engine/producer.py` → `ProducedFlag.content()`) writes exactly this body;
the trigger (`skills/iga-proactive/engine/triggers.py` →
`parse_flag_content` + `eval_mempalace`) reads the same fields back out of
`content`/`content_preview`. Producer-write and trigger-read are one shape
end to end. Legacy `drawer.metadata.<field>` still **wins when present**
(backward compatible with the old scanner / test fakes), with the
content-parsed value as the fallback that makes it work against the live MCP.

### Canonical `content` body

```
NEWSLETTER-RESEARCH-QUEUE FLAG
hook_name: <slug matching rules/hooks/<slug>.md>
title: <human label, e.g. "Newsletter/Dev: Weekly digest">
target_date: <YYYY-MM-DD>
message-id: <Gmail message id>
triggered: false
label: <Gmail label>            # optional
gmail_query: <the query that matched>   # optional
```

| Field | Required | Read by trigger as | Notes |
|---|---|---|---|
| `NEWSLETTER-RESEARCH-QUEUE FLAG` | Yes | (banner, ignored) | First line; human/debug marker |
| `hook_name` | Yes | `drawer.hook_name` | Worker loads `rules/hooks/<hook_name>.md` |
| `title` | Yes | `drawer.title` / candidate title | |
| `target_date` | Yes | `drawer.target_date` | Part of the idempotency key |
| `message-id` | Yes | `drawer.message_id` | The email the worker fetches |
| `triggered` | No (`false`) | skip-if-`true` marker | Lets a consumed flag be tombstoned |
| `label` / `gmail_query` | No | `drawer.context` | Provenance for the worker |

- **Filing wing:** `iga/newsletter-research` (bookkeeping only — the trigger
  is room-scoped, so the wing just keeps the content-addressed drawer id
  stable). Room: **`newsletter-research-queue`** (binding — this is the
  trigger room and the killswitch surface).
- **Idempotency:** the drawer id is `sha256(wing+room+content)`; an identical
  re-file is a server-side no-op. The producer *also* holds a ledger claim
  keyed `nl-produce::<hook>::<message-id>` so it does not re-hit the MCP every
  tick. The consumer separately dedups via its own 72h cooldown on
  `newsletter::{{drawer.id}}::{{drawer.target_date}}`.
- **Killswitch unchanged:** an empty `newsletter-research-queue` room still
  spawns nothing. The producer only ever *adds* flags when a real hook
  matches a real message; it never alters the empty-room semantics.

### Unhooked-cluster offer

`engine/unhooked.py` is a sibling detector: it counts high-value newsletter
streams in labeled mail **not covered by any `rules/hooks/*.md`** and, when a
threshold is crossed, writes exactly ONE `surface_next_brief` offer to the
gitignored `~/Iga/scratch/newsletter-unhooked-offer.json` (override:
`$IGA_NL_UNHOOKED_OFFER`). Cluster identities are **salted-SHA1 hashed** —
no sender/domain/subject ever reaches disk or the surfaced text (PII
contract). Honours `IGA_PROACTIVE_RESEARCH=0` / `IGA_PROACTIVE_SPAWN=0`
exactly like the producer/consumer.

## Three-layer separation

| Layer | Where | Committed? |
|---|---|---|
| Generic runner | `skills/newsletter-research/` | Yes (OSS) |
| Example spec | `skills/newsletter-research/examples/` | Yes (PII-free) |
| Personal hooks | `rules/hooks/<name>.md` | **No** (gitignored) |

The runner never hardcodes any hook's content — the hook spec IS the
configuration. Install the runner once; author as many hooks as needed.

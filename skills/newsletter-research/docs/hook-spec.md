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
                                    # are filed (room: findings).

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
| `output_wing` | Yes | string | non-empty |
| `cadence` | No | string | "on-demand" or "auto", default "on-demand" |
| `status` | No | string | "active" or "paused", default "active" |

A spec that fails validation raises `HookSpecError` with a descriptive
message — the engine surfaces this as a job-load error (same path as
`SchemaError` in `schema.py`), never crashes silently.

---

## Three-layer separation

| Layer | Where | Committed? |
|---|---|---|
| Generic runner | `skills/newsletter-research/` | Yes (OSS) |
| Example spec | `skills/newsletter-research/examples/` | Yes (PII-free) |
| Personal hooks | `rules/hooks/<name>.md` | **No** (gitignored) |

The runner never hardcodes any hook's content — the hook spec IS the
configuration. Install the runner once; author as many hooks as needed.

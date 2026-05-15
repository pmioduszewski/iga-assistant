# Notion Rules

## Where things go (Notion vs MemPalace routing)

Notion and MemPalace serve different roles. Pick one based on **how the data is used**, not on tier.

### Write to Notion when:
- Data has UI value — user opens it on phone, glances at it, sorts/filters it
- Has structured properties or formulas (databases, finance ledgers, project boards)
- User reads it **without Claude in the loop**
- Examples: finances, project dashboards, reading lists, habit trackers (if reviewed visually)

### Write to MemPalace when:
- Reference-only, Gaia is the primary reader
- Needs semantic recall (cross-cutting context, "what did X say about Y")
- Facts about people, decisions, corrections, preferences, behavioral patterns
- Examples: meeting notes, call transcripts, session summaries, behavioral rules, daily diary

### Never duplicate
The same fact should live in **one** place. Duplicates drift. If something is in MemPalace `gaia/rules`, do not also write it to a Notion "Rules" page.

## Specific routing for meeting / call / transcript content

**Meeting notes, call transcripts, voice memos, session summaries → MemPalace, never Notion.**

These are reference-only by nature, semantic-search-shaped, and burn tokens disproportionately when stored in Notion (each retrieval = full page fetch).

Store under:
- `sessions/meetings` for meetings
- `people/<name>` rooms for 1:1s and call notes about a person
- Use AAAK format for compression — see `gaia_memory_protocol.md`

When user shares a transcript or meeting recap mid-conversation:
1. `mempalace_add_drawer` immediately to the right wing/room
2. Do NOT create a Notion page for it
3. Confirm by drawer ID, not by Notion URL

## Page creation in Notion

Always ask which workspace and parent page before creating a new page. Never create pages at the root level without explicit instruction.

Before creating a new page, ask yourself: **"Will the user open this on their phone or in a browser?"** If no, route to MemPalace instead.

## Content style

- Use headings (H2, H3) for structure — never walls of text
- Keep pages scannable — someone should get the gist in 10 seconds
- Use callout blocks for warnings or important notes
- Tables for structured data, not bullet lists

## Databases

Before adding entries to a database, fetch the schema first (properties, types, options). Never guess property names or option values.

## Search before create

Before creating a new page, search for existing pages with similar titles. Suggest updating an existing page instead of creating a duplicate.

## Token efficiency

Notion fetches are expensive (5–10K tokens per page). To minimize cost:
- **Cache page IDs** in MemPalace `reference/notion` once you know them — don't re-search every session
- Prefer **`update_content` with `old_str`/`new_str`** over `replace_content` (replace re-sends the full page)
- For audits, list ancestors/children via search before fetching full page bodies
- If you only need one section, fetch the page once and remember the structure

## Hygiene / archival

- Pages older than 90 days that haven't been read or updated → suggest archiving to a `Log & Archive` subpage
- Research docs whose decisions are made → archive once decisions are codified elsewhere (MemPalace, repo, or another stable home)
- Behavioral rules and corrections live in MemPalace `gaia/rules`. Any Notion "rules" page is duplicate state and should be deprecated.

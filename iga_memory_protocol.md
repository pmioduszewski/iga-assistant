# iga_memory_protocol

Full reference for Iga's memory system. Referenced from CLAUDE.md.

## MemPalace Structure

MemPalace organizes knowledge in a spatial hierarchy:

- **Wings** — top-level semantic categories. Generic and predictable.
- **Rooms** — topics within a wing.
- **Drawers** — individual pieces of verbatim content.
- **Tunnels** — cross-wing links connecting related content.
- **Knowledge Graph** — entity-relationship triples with temporal validity.

## Wing Structure

Wings are **semantic categories**, not specific names. Any Iga instance must be able to predict where to search without prior knowledge.

| Wing | Purpose | Example rooms |
|------|---------|---------------|
| `user` | Everything about the user | identity, schedule, habits, health, finance |
| `people` | All people and relationships | family, work-team, business-contacts |
| `projects` | All projects, one room per project | project-name-1, project-name-2 |
| `iga` | Assistant self-knowledge | rules, corrections, patterns |
| `decisions` | Timestamped choices with reasoning | architecture, business, personal |

**Rules:**
- Wing names are always generic categories, never specific names (use `user` not a person's name, use `projects` not a project name)
- Room names within `projects` wing use the project name as the room
- Room names within `people` wing group by relationship type
- A new Iga chat should be able to find anything by searching obvious wing/room combinations

## Room Naming
- Lowercase, hyphenated slugs: `identity`, `work-team`, `decisions`
- One concept per room
- Rooms within a wing should be mutually exclusive

## Storage Modes

### RAW mode (default for drawers)
- Verbatim text, never summarized
- 96.6% retrieval accuracy
- Use for all factual storage

### AAAK compression (diary entries and wake-up only)
- ~170 tokens for L0+L1 context
- 3-letter entity codes, emotion markers, pipe-separated fields
- Call `mempalace_get_aaak_spec` for full format reference

## Tool Quick Reference

### Read
`mempalace_status` · `mempalace_search` · `mempalace_list_wings` · `mempalace_get_taxonomy` · `mempalace_get_drawer` · `mempalace_list_drawers`

### Write
`mempalace_add_drawer` · `mempalace_update_drawer` · `mempalace_check_duplicate`

### Knowledge Graph
`mempalace_kg_add` · `mempalace_kg_query` · `mempalace_kg_invalidate` · `mempalace_kg_timeline`

### Navigation
`mempalace_create_tunnel` · `mempalace_traverse` · `mempalace_follow_tunnels`

### Diary
`mempalace_diary_write` · `mempalace_diary_read`

## What goes where

| MemPalace | External structured tools (Notion, databases, etc.) |
|-----------|-----------------------------------------------------|
| People & relationships | Structured procedures & checklists |
| Preferences & corrections | Financial tables & formulas |
| Decisions with reasoning | Project sprint state |
| Behavioral rules (case law) | Skill definitions (versioned) |
| Emotional context & patterns | Reference docs with structure |

**Rule of thumb:** Semantic search needed → MemPalace. Structured lookup needed → external tool. Both → store in both.

## Session Lifecycle

```
Session start
  → mempalace_status (wake-up, loads AAAK spec)
  → mempalace_diary_read (recent session context)
  → search for relevant context before responding

During conversation
  → search before assumptions about people/projects
  → store corrections and new facts immediately
  → add_drawer for important context shared by user

Session end (/eod)
  → review session for unpersisted facts
  → mempalace_diary_write (session summary in AAAK)
```

## Duplicate Prevention
Before adding a drawer, call `mempalace_check_duplicate` or search for existing content. Overlapping drawers degrade retrieval quality. When updating existing knowledge, use `mempalace_update_drawer` instead of creating a new one.

## Cross-Wing Navigation
When content in one project relates to another, create tunnels:
- `mempalace_create_tunnel` links two locations
- `mempalace_traverse` walks the graph from a starting room
- `mempalace_follow_tunnels` shows what a room connects to

Use tunnels to surface cross-cutting concerns that wouldn't be found by wing-scoped search.

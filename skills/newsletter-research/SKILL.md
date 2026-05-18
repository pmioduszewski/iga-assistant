---
name: newsletter-research
description: Generic email hook runner — extracts artifacts from labeled mail, scores them against a user-authored hook spec (interest_profile + scoring_context), and files high-fit findings to the MemPalace Knowledge Vault.
intent_triggers:
  - newsletter research
  - triage mail
  - research newsletter
  - newsletter findings
  - email hook
prerequisites:
  - name: mempalace-server
    description: Required for filing vault findings and reading the newsletter-research-queue flag trigger.
    check: mcp(IgaMemory)
    severity: error
  - name: gmail-mcp
    description: Worker reads the labeled message body via the Gmail/Workspace MCP. Absent → worker can't fetch the email (job stays dormant, not an error).
    check: mcp(iga-gmail)
    severity: warning
  - name: proactive-engine
    description: This skill's proactive.yaml is discovered/gated by the generic skills/iga-proactive engine. Without it nothing runs.
    check: file(~/Gaia/skills/iga-proactive/engine/runtime.py)
    severity: warning
triggers:
  - kind: hook
    spec: generic iga-proactive engine discovers skills/newsletter-research/proactive.yaml; the newsletter-research-queue MemPalace room is the deterministic, OFF-by-default trigger (empty room = nothing spawned)
  - kind: manual
    spec: "/triage-mail and /research-newsletter <message-id> — manual fire pre-automation"
mempalace_wings:
  - vault/*
  - "*/newsletter-research-queue"
mcp_dependencies:
  - IgaMemory
  - iga-gmail
widgets:
  - id: newsletter-findings
    type: message
    title: Newsletter R&D
    data_source: ~/Gaia/state/widgets/newsletter-research-findings.json
    refresh: 300
    coach:
      tone: neutral
      text_field: coach
status: spec
proactive: see ./proactive.yaml (the generic engine discovers skills/*/proactive.yaml; the safety gate + job contract live there and in § Killswitch below)
---

# Email Hook Runner — Generic

A generic hook runner that triggers on labeled mail, extracts artifacts, scores
fit against a **user-authored hook spec**, and files high-fit findings to a
MemPalace Knowledge Vault wing.

The runner is OSS-clean and opinion-free. All the *what-to-look-for* and
*why-it-matters* lives in a **hook spec file** the user authors (personal
layer, gitignored). One runner; as many hooks as needed.

Locked via `/new-skill` meta-template (`skills/create-iga-skill/SKILL.md`).

Mirrors the proven `skills/iga-proactive-research` structure: a `proactive.yaml`
the generic `skills/iga-proactive` engine discovers, a single-shot
`engine/worker.prompt.md`, stdlib-only deterministic helpers
(`engine/extract.py`, `engine/hook_spec.py`), and unit tests.

## Hook spec — what it is and where it lives

A hook spec is a Markdown file (YAML frontmatter + optional body) that tells
the worker:

- **`interest_profile`** — free-form natural language: what the user cares
  about (e.g. "libraries that could improve my software projects" OR
  "practical parenting tips for a toddler" OR "kids clothing promotions under
  150 PLN"). The worker uses this verbatim as its evaluation lens.
- **`scoring_context`** — list of MemPalace wing/room globs to query for
  semantic relevance evidence (e.g. `["projects/*"]` or `["family", "user/*"]`).
- **`trigger`** — which Gmail label/query triggers this hook.
- **`output_wing`** — where high-fit findings are filed in MemPalace.
- Other fields: `fit_threshold`, `cadence`, `status`. Full schema in
  `skills/newsletter-research/docs/hook-spec.md`.

### Three-layer separation

| Layer | Where | Committed? |
|---|---|---|
| Generic runner | `skills/newsletter-research/` | **Yes** (OSS) |
| Example spec | `skills/newsletter-research/examples/example-hook.md` | **Yes** (PII-free) |
| Personal hooks | `rules/hooks/<name>.md` | **No** (gitignored) |

The runner never reads a hardcoded hook — it reads the spec from the flag
drawer's `hook_name` metadata → `rules/hooks/<name>.md`. The `rules/hooks/`
path is the personal layer (gitignored); do NOT create any `rules/hooks/*`
file here. Personal hooks are the user's responsibility.

### Retirement note

`skills/email/src/hooks/newsletter-research.ts` (if present) is superseded
by this generic Python runner. The TypeScript hook should not be used for
new work. Do NOT edit `skills/email/**` (gitignored, out of scope).

## Killswitch (BINDING — this skill is OFF by default)

The generic engine **discovers** `proactive.yaml` (it parses, validates, and
appears in a scan) but **spawns nothing unattended**:

- **The trigger is a MemPalace room poll** (`newsletter-research-queue`),
  not a live email-label poll. **The room is empty by default → zero
  candidates → zero workers.** The empty room *is* the killswitch. This is
  the exact safety property `iga-proactive-research`'s
  `research-mempalace-queue` job relies on.
- Belt-and-braces engine-wide: `IGA_PROACTIVE_SPAWN=0` (the documented
  detect-but-don't-mutate killswitch, shared with the research port) also
  suppresses every spawn globally.

**How to turn it ON (when awake — do NOT do this unattended):**

1. Author a hook spec in `rules/hooks/<name>.md` (personal, gitignored).
   See `skills/newsletter-research/docs/hook-spec.md` for the schema.
2. Pick a labeled email that matches your hook's trigger.
3. File a MemPalace flag drawer:

   ```python
   mempalace_add_drawer(
     wing="...", room="newsletter-research-queue",
     metadata={
       "title": "<email subject>",
       "target_date": "YYYY-MM-DD",
       "hook_name": "<your-hook-slug>",  # matches rules/hooks/<slug>.md
     },
     content="message-id: <id>; label <Gmail-label>"
   )
   ```

4. Next `/gm` or `/back` scan → the engine fires exactly **one** gated
   worker for it (`cooldown: 72h` ledger guard = no duplicate).
5. To pause: stop filing flag drawers (or set `IGA_PROACTIVE_SPAWN=0`).
   Deleting all `newsletter-research-queue` drawers returns it to dormant.

No code edit is needed to flip it either way — the gate is data.

## Purpose

High-volume email streams (newsletters, digests, alerts) are unrealistic to
read end-to-end. The hook extracts *artifacts* worth remembering — things
matching the hook's `interest_profile` — scores each against the user's
`scoring_context` wings, and files high-fit findings to MemPalace for later
surfacing during focused work.

When the user is stuck on a decision, Iga can surface: *"N findings from the
email backlog match this context — skim?"*

## Scoring (generic, spec-driven)

The 0–3 scale is defined relative to **`interest_profile` + `scoring_context`**:

- **3** — directly matches active work or strong stated interest (semantic
  match in `scoring_context` wings confirms active relevance)
- **2** — matches general interest area (same domain/category, clearly relevant)
- **1** — tangential; marginally related
- **0** — no fit

Threshold (`fit_threshold`, default 2) — drop 0/1 entries. Cap: ≤5 findings
per message.

## Surfacing rules

- **At `/gm`:** if 3+ new high-fit findings landed since last `/gm`, surface a
  1-line nudge: *"📚 5 R&D findings filed since yesterday — 3 fit `<context>`. Skim?"*
- **At `/focus <project>`:** surface top-3 unread findings for that project
  (`status: new` + matching project)
- **On-demand:** `/findings <project>` lists all `status: new` findings for
  that project
- **Never inject during deep work blocks, debug sessions, or burnout-spiral days**
- **Never re-surface a finding after it's been marked `status: reviewed`**

## Editorial discipline

- **Don't auto-generate review-quality summaries.** Extract + cite + tag only.
- **Don't duplicate** — `mempalace_check_duplicate` before filing.
- **Per-email budget:** ≤5 artifacts filed per message. If a newsletter has
  20 links, keep the 5 with highest fit score.
- **Source-cite every finding.** Email name + message ID. The user can audit.

## Cost model (budget, NOT enforcement)

Per-message processing:
- Body fetch: ~2-10k tokens input
- Per-link fetch (avg 3 per message): ~5-15k tokens
- Vault query for fit scoring: ~3-5k tokens
- Filing: ~2k tokens
- **Total per message:** ~15-40k tokens

At Sonnet 4.5 rates ($3/M input, $15/M output): **~$0.10-0.30 per email**

## User-specific config

All user-specific config lives in the hook spec (`rules/hooks/<name>.md`,
gitignored). The generic runner has no opinion on what's interesting.

User may also scope/filter via `SKILL.local.md` (gitignored). The engine
reads it at runtime. The runner itself stays generic.

## Adherence tracking

- MemPalace `iga/architecture/skills-inventory` drawer (canonical) tracks:
  status, last fired, count of findings filed.
- Optional: monthly `/eow` Sunday flow reviews findings-filed vs
  findings-reviewed ratio. If reviewed < 20%, fit-threshold may be too loose.

## Connects to

- `vault/*` MemPalace wings (Knowledge Vault)
- `skills/iga-proactive/engine` (discovers proactive.yaml)
- MemPalace `iga/architecture/skills-inventory`
- `rules/hooks/` (personal hook specs — gitignored, not in this repo)

## OSS-clean separation

- `skills/newsletter-research/` engine: generic artifact extraction, fit scoring,
  vault filing — NO user data, NO hardcoded interests
- `skills/newsletter-research/SKILL.md` (this file): generic runner spec
- `skills/newsletter-research/SKILL.local.md` (gitignored): user-personal config
  scoping or extending the runner
- `rules/hooks/<name>.md` (gitignored): personal hook specs (interest_profile,
  scoring_context, trigger — all user-authored)
- `community_skills/newsletter-research/` (future): redacted installable template

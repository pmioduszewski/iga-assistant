# Newsletter Research Hook

Iga hook that triggers on labeled Newsletter mail, extracts artifacts, scores fit against the user's MemPalace projects, and files high-fit findings to a Knowledge Vault wing.

Locked via `/new-skill` meta-template (`skills/create-iga-skill/SKILL.md`).

## Purpose

A high-volume newsletter stream (30–60 emails/month for a typical research-oriented user) is unrealistic to read end-to-end. The hook extracts the *artifacts* worth remembering — libraries, GitHub repos, tools, techniques, blog posts — scores each against the user's active projects (declared in `SKILL.local.md`), and files high-fit findings to MemPalace `vault/<project>` for later surfacing during focused work.

When the user is stuck on a project decision, Iga can surface: *"N R&D findings from the Newsletter/Dev backlog match this context — skim?"*

## Trigger

- **Sub-labels enabled:** `Newsletter/Dev`, `Newsletter/Business`
- **Disabled:** `Newsletter/Design`, `Newsletter/News`
- **Fire timing:**
  - **Pre-June-15:** manually via `/triage-mail` command (Sonnet on Claude MAX subscription, predictable cost)
  - **Post-June-15:** auto on every newly-labeled `Newsletter/Dev` or `Newsletter/Business` arrival (Anthropic Agent SDK credit pool, $100/mo budget)

## Permissions

When the hook fires on a message, Iga is granted:
- Read full body (`manage_email read` with `bodyFormat: html` for tracking-pixel-aware sanitization)
- Fetch linked URLs (WebFetch) — bounded to 5 URLs per message max
- Web search for context (WebSearch) — bounded to 2 queries per message
- File to MemPalace (`mempalace_add_drawer`)
- File to Notion (when Knowledge Vault mirror ships)

## Action — per message

1. **Read body** (sanitized HTML, plain-text fallback)
2. **Extract artifacts** — for each artifact mentioned:
   - Name + identifier (e.g. `tanstack/router`, `Drizzle ORM`, `react-aria`)
   - Type: `lib` / `repo` / `tool` / `technique` / `blog-post` / `talk` / `paper` / `service`
   - Source URL (primary, if linked)
   - 1-sentence what-it-is from context
3. **Per artifact, query MemPalace** `projects/*` wings for matching signal
   - Semantic match across project drawers
   - Score 0-3:
     - **3**: directly matches active work (e.g. "drizzleORM tip" + the user has an active drizzleORM migration)
     - **2**: matches general project category (e.g. "React Compiler" + a web-frontend project the user owns)
     - **1**: tangentially relevant
     - **0**: no fit
4. **Apply fit threshold ≥2** — drop 0/1 entries
5. **File each surviving artifact** as a drawer in `vault/<best-fit-project>`:
   - Schema: title, URL, type, project-fit-score, why-it-fits (1 sentence Iga rationale), source-newsletter, source-message-id, date-found, status (`new`)

## Surfacing rules

- **At `/gm`:** if 3+ new high-fit findings landed since last `/gm`, surface a 1-line nudge: *"📚 5 R&D findings filed since yesterday — 3 fit `<project>`. Skim?"*
- **At `/focus <project>`:** surface top-3 unread findings for that project (`status: new` + matching project)
- **On-demand:** `/findings <project>` lists all `status: new` findings for that project
- **Never inject during deep work blocks, debug sessions, or burnout-spiral days**
- **Never re-surface a finding after it's been marked `status: reviewed` by the user**

## Editorial discipline

- **Don't auto-generate review-quality summaries.** Iga's job is extract + cite + tag, not editorialize. The user reads the source if it's worth it.
- **Don't duplicate** — `mempalace_check_duplicate` before filing.
- **Per-newsletter budget:** ≤5 artifacts filed per message. If a newsletter has 20 links, pick the 5 with highest project-fit.
- **Source-cite every finding.** Newsletter name + message ID. The user can audit.

## Cost model (budget, NOT enforcement)

Per-message processing:
- Body fetch: ~2-10k tokens input
- Per-link fetch (avg 3 per message): ~5-15k tokens
- Vault query for fit scoring: ~3-5k tokens
- Filing: ~2k tokens
- **Total per message:** ~15-40k tokens

At Sonnet 4.5 rates ($3/M input, $15/M output): **~$0.10-0.30 per newsletter**

Monthly volume (Newsletter/Dev + /Business): ~30-40 messages = **~$3-12/month**

Well under the user's $100/mo Agent SDK credit allocation for this use case.

## User-specific config

This section is intentionally minimal in the engine spec. User-specific lists of active projects, topics-to-include, and topics-to-exclude live in `SKILL.local.md` (gitignored). The engine reads them at runtime; the engine itself stays generic.

- **Active projects** (for fit scoring) — Iga reads from MemPalace `projects/*` wings dynamically; the user can scope or filter via `SKILL.local.md`.
- **Topic include/exclude lists** — declared in `SKILL.local.md`. The engine has no opinion on what's interesting; that's user preference.

## Adherence tracking

- MemPalace `gaia/architecture/skills-inventory` drawer (canonical) tracks: status, last fired, count of findings filed
- Optional: monthly `/eow` Sunday flow reviews findings-filed vs findings-reviewed ratio. If reviewed < 20%, the hook's surfacing is too aggressive or the fit-threshold too loose.

## Open questions

- Should the hook also fire on **Status** labeled messages (vendor product updates from tools the user uses)? Possibly — but lower density. Defer until v2.
- Notion mirror — when does it ship? Tied to Knowledge Vault Notion DB schema task `6gfFhW47CjcWgmfx`. Until then, MemPalace-only.

## Connects to

- `skills/newsletter-research/` engine (forthcoming) — bundled inside `skills/email/`
- `vault/*` MemPalace wings (Knowledge Vault)
- `rules/email/taxonomy.md` (forthcoming — Newsletter sub-labels canonical)
- MemPalace `gaia/architecture/skills-inventory`
- Agent SDK budget allocation rules

## OSS-clean separation

- `skills/newsletter-research/` engine (forthcoming): artifact extraction, fit scoring, vault filing, batch processing — generic, no user data
- `skills/newsletter-research/SKILL.md` (this file): generic engine spec — enabled sub-labels, fit threshold defaults, capture pattern
- `skills/newsletter-research/SKILL.local.md` (gitignored): user-personal config — concrete project list, include/exclude topics
- `community_skills/newsletter-research/` (future): redacted installable template

## Manual capture today

Even before the engine ships, the user can:
- `/research-newsletter <message-id>` — Iga reads the message, does steps 1-5 manually for that one email, files findings
- Use this to test the hook on real newsletters and tune fit-threshold before automating

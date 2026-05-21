---
name: email
description: Iga email triage engine — pre-filter + Sonnet classifier + Gmail batch labeling + hooks
intent_triggers:
  - "triage my email"
  - "triage my inbox"
  - "triage my emails"
  - "process my inbox"
  - "sort my email"
  - "label my email"
  - "label my emails"
  - "sync label colors"
  - "recommend label colors"
  - "ensure labels"
  - "/triage-mail"
  - "/iga-mail"
prerequisites:
  - name: oauth-credentials
    description: OAuth refresh tokens for each Gmail account, one JSON per account at ~/.local/share/iga-email/credentials/<slug>.json
    check: file(~/.local/share/iga-email/credentials)
    guide: docs/setup-oauth.md
    severity: error
  - name: claude-cli
    description: Local `claude` CLI for headless classifier subprocess calls
    check: cmd(claude)
    severity: error
  - name: pnpm
    description: Package manager — engine ships with pnpm-lock.yaml
    check: cmd(pnpm)
    guide: docs/setup-pnpm.md
    severity: warning
  - name: node-22-plus
    description: Node.js 22+ required (ESM, native test runner, --import flag)
    check: cmd(node)
    severity: error
  - name: rules-email-config
    description: Per-user account list + taxonomy + sender rules — the user-personal config consumed by the engine
    check: file(rules/email/accounts.md)
    guide: docs/setup-rules.md
    severity: warning
  - name: launchd-agent-loaded
    description: Daily auto-triage requires the LaunchAgent com.iga.email-triage to be loaded
    check: launchctl list 2>/dev/null | grep -q com.iga.email-triage
    guide: docs/setup-launchd.md
    severity: warning
  - name: launchd-wrapper-executable
    description: Wrapper script must be executable (install.sh sets this)
    check: file(engine/launchd/iga-email-triage, mode=0755)
    guide: docs/setup-launchd.md
    severity: warning
  - name: pmset-wake-scheduled
    description: macOS must wake before 06:00 for the LaunchAgent to fire (only relevant if the Mac sleeps)
    check: pmset -g sched 2>/dev/null | grep -qiE "wake|poweron"
    guide: docs/setup-launchd.md#pmset-wake
    severity: info
  - name: triage-fired-today
    description: Verify the morning triage actually ran (drift detection — if false, the schedule is broken even though all installs look fine)
    check: file(~/Library/Logs/iga/email-triage-$(date +%Y-%m-%d).json)
    guide: docs/setup-launchd.md#daily-verification
    severity: info
triggers:
  - kind: slash-command
    spec: "iga-mail triage [--account <alias>] [--apply] [--json]"
  - kind: slash-command
    spec: "iga-mail labels list|create|ensure"
  - kind: slash-command
    spec: "iga-mail filters list|create|delete"
  - kind: slash-command
    spec: "iga-mail trash <messageId...>"
  - kind: mcp-tool
    spec: "mcp__iga-email__triage / search / read / labels_* / filters_* / archive / delete"
  - kind: scheduled
    spec: "launchd LaunchAgent — see engine/launchd/com.iga.email-triage.plist (Phase 2)"
  - kind: auto
    spec: "matches any intent_triggers pattern in a user message → Iga calls mcp__iga-email__triage"
mempalace_wings:
  - projects/iga
mcp_dependencies: []
status: shipped
---

# Iga Email Engine

Direct Gmail API engine (googleapis npm). Pre-filter rules + Sonnet classifier + real Gmail `batchModify` + filter management + label color sync. CLI surface (`iga-mail`) and MCP surface (`iga-email`).

## Purpose

the user runs four Gmail accounts (work/personal/biz/umbrella). Triaging them manually is a daily 15-30 min tax. This skill compresses it to ~30 seconds of Sonnet classification + one Gmail `batchModify` call per account. the user can run it interactively ("Iga, triage my work inbox") or via launchd at 06:00 so the inbox is groomed before he wakes.

## Architecture

```
listUnread (googleapis)             pre-filter rules
       ↓                            (rules/email/{accounts,taxonomy}.md)
   GmailMessage[]  ────►   matched → decision: pre-filter
                          unmatched ↓
                          claude -p --model claude-sonnet-4-6
                          (batches of 15, ≤3k tokens, zod-validated)
                                    ↓
                          decision: llm OR fallback
                                    ↓
                          batchApplyLabels (real Gmail batchModify, ≤1000 ids/call)
```

## Capture surface

| How | Example |
|---|---|
| CLI subcommands | `iga-mail triage --account work --apply --json` |
| MCP tools | `mcp__iga-email__triage({account:["work"], dryRun:false})` |
| Natural-language auto-invoke | "Iga, triage my personal inbox" → calls MCP tool |
| Scheduled (Phase 2) | launchd fires `iga-mail triage --apply --account all` daily |

## Storage

| Wing/room | What | When |
|---|---|---|
| `projects/iga` | Ship memos, design decisions | At significant milestones only |
| (none routine) | Triage decisions live in Gmail labels, not MemPalace | by design — Gmail IS the storage |

Triage output is intentionally NOT persisted to MemPalace per session. Gmail labels are the source of truth. The engine emits a JSON report to stdout/stderr for the caller (CLI or MCP) to consume; nothing is filed automatically.

The newsletter-research hook (separate skill: `skills/newsletter-research/`) is related — when fired, its handler emits a research-digest payload (URLs, body preview, message id) to stdout for the caller/conversational layer to act on; it does NOT itself write to MemPalace.

## Surfacing rules

- **Default mode:** silent. the user invokes; engine runs; report goes back to the caller.
- **Auto-invoke:** when the user's message matches `intent_triggers`, call `mcp__iga-email__triage` with sensible defaults (`dryRun: false` only if his phrasing is unambiguous about applying; otherwise `dryRun: true` and show preview).
- **Scheduled mode (Phase 2):** runs autonomously. Output logs to `~/Library/Logs/iga/email-triage-<date>.json`. Summary surfaced in next `/gm`.

## Configuration sources (the OSS-clean separation)

| Layer | Where | Owned by |
|---|---|---|
| Engine code | `skills/email/src/` | Skill (OSS-publishable) |
| Account list, sender rules | `rules/email/accounts.md` | the user-personal data — gitignored |
| Taxonomy + label colors | `rules/email/taxonomy.md` | the user-personal data — gitignored |
| Optional override of default pre-filter rules | `rules/email/overrides.md` | the user-personal — see `rules/email/overrides.md.example` |
| Skill instructions (this file) | `skills/email/SKILL.md` | OSS-publishable, no the user data |
| the user-personal SKILL overrides | `skills/email/SKILL.local.md` | the user — gitignored, optional |
| OAuth tokens | `~/.local/share/iga-email/credentials/<slug>.json` | Outside repo, file-permissioned |

Engine reads `rules/email/*` at runtime via `IGA_RULES_DIR` env or auto-discovery (walks up parents looking for `rules/email/accounts.md`). No the user data lives in `skills/email/` itself.

## MCP tools (when Iga is loaded)

| Tool | Purpose | Mutates? |
|---|---|---|
| `triage` | Pre-filter + LLM classify; apply labels via batchModify if `dryRun: false` | Yes (when `dryRun: false`) |
| `labels_list` | List Gmail labels with colors | No |
| `labels_create` | Create a single label, optionally with color | Yes |
| `labels_ensure` | Sync canonical labels (create missing, patch colors) per `rules/email/taxonomy.md` | Yes (when `dryRun: false`) |
| `filters_list` | List Gmail filters | No |
| `filters_create` | Create a Gmail-side filter | Yes |
| `filters_delete` | Delete one or more filters; requires `confirm: true` | Yes |
| `delete` | batchDelete messages; requires `confirm: true` (CLI alias: `trash`) | Yes |

Safety defaults: `triage.dryRun = true`. Destructive ops require `confirm: true`.

## How a typical session goes

1. the user: "Iga, triage my personal inbox."
2. Iga: calls `mcp__iga-email__triage({account: ["personal"], dryRun: true, maxResults: 25})`
3. Iga: shows compressed summary table of decisions; asks via AskUserQuestion if he wants to apply
4. On confirm: same call with `dryRun: false`
5. Reports labels applied + any `missingLabels` (suggests `labels_ensure` if any)

## Open questions

- Hook auto-trigger remains manual (`--run-hooks` flag). Auto-fire on classifier output is a v3 concern (needs guardrails).
- Newsletter-research integration is one-way: triage tags, hook reads tags. Currently triggered manually.
- The `--apply` decision threshold (confidence ≥ X) is implicit (engine applies whatever LLM returns). Could add a min-confidence config knob.

## Connects to

- `skills/newsletter-research/SKILL.md` — consumes Newsletter-labeled mail
- `rules/email/{accounts,taxonomy}.md` — runtime config
- MemPalace `projects/iga` — ship memos
- Todoist Iga project (`6gc9wc4gHMV5R3fc`) — engine tasks tracked here

## Composability self-check (per create-iga-skill checklist)

- [x] Frontmatter complete: name, description, intent_triggers, prerequisites, triggers, mempalace_wings, mcp_dependencies, status
- [x] All prereqs declared in `prerequisites:` — `/gaia status` will find them
- [x] All triggers declared
- [x] `intent_triggers` set — the user can auto-invoke by natural phrase
- [x] CLAUDE.md does not name this skill — generic discovery only
- [x] Setup guides: `docs/setup-oauth.md`, `docs/setup-pnpm.md`, `docs/setup-rules.md`, `docs/setup-launchd.md` (Phase 2)
- [x] OSS clean — the user-specific data lives in `rules/email/`, never in `skills/email/`

Status: **shipped 2026-05-14**. Phase 2 (launchd daily auto-triage) in progress.

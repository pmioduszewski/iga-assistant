# @iga/email — Email Triage Engine

Engine that labels Gmail inboxes using a deterministic pre-filter + a batched
Sonnet 4.5 classifier, then optionally dispatches per-label hooks.

The engine is OSS-clean — all per-user config (account aliases, label
preferences, per-sender rules) lives in `rules/email/` and `rules/hooks/`.

## Architecture

```
unread → pre-filter (rules table) ──► matched? → decision
                                          │
                                          └─► no
                                                ▼
                                          LLM classifier (batched Sonnet 4.5)
                                                │
                                                ▼
                                          label-resolver (name → id per account)
                                                │
                                                ▼
                                          Gmail.applyLabels (via iga-gmail MCP)
                                                │
                                                ▼ (if --run-hooks)
                                          hook-runner → newsletter-research / …
```

## Install

```bash
cd skills/email
npm install
```

## Smoke test (no Gmail auth needed)

```bash
# Run unit tests
npm test

# Run a full dry-run against mock fixtures
IGA_EMAIL_MOCK=1 npm run triage-mail -- --mock --dry-run --json
```

## Live use (when MCP wiring lands)

Today, `src/gmail.ts` is a documented shim. The engine knows the exact
`manage_email` MCP calls it would make and logs them. Set `IGA_EMAIL_MOCK=1`
for fixture-driven runs.

In v2 the engine will either spawn the `iga-gmail` MCP as a sibling process
or be invoked from within an Anthropic Agent SDK runtime that has the MCP
preloaded. The pre-filter / classifier / hook-runner logic does not change.

## CLI

```
triage-mail [options]

  --account <alias>   limit to one account (repeatable). e.g. work, personal, biz, umbrella
  --max <n>           unread messages per account (default: 25)
  --batch-size <n>    LLM batch size, 10-20 (default: 15)
  --dry-run           classify but don't apply Gmail labels
  --run-hooks         dispatch matching hooks (e.g. newsletter-research)
  --mock              use mock fixtures (also: IGA_EMAIL_MOCK=1)
  --json              emit JSON report to stdout
  -h, --help          show this help
```

## Files

- `src/cli.ts` — entry point
- `src/triage.ts` — orchestrator
- `src/pre-filter.ts` — deterministic rule table (first-match-wins)
- `src/classifier.ts` — batched Sonnet 4.5 via `claude -p`
- `src/gmail.ts` — `iga-gmail` MCP wrapper (v1 stubs + mock)
- `src/label-resolver.ts` — name → id cache per account
- `src/hook-runner.ts` — parse `rules/hooks/*.md`, dispatch matching hooks
- `src/hooks/newsletter-research.ts` — first hook (v1: emits digest JSON)
- `src/config-loader.ts` — parses `rules/email/*.md` into runtime config
- `src/types.ts` — shared types + zod schemas

## Hooks

A hook is `rules/hooks/<name>.md` (markdown spec) + `src/hooks/<name>.ts`
(handler). Triggers are parsed from the spec's `## Trigger` section
(backticked labels after `Sub-labels enabled:`).

Current hooks:

- `newsletter-research` — triggers on `Newsletter/Dev`, `Newsletter/Business`.
  v1 emits a structured digest (URLs, body preview, message id) for the
  conversational layer to extract artifacts and file to MemPalace.

## OSS-clean separation

Engine has zero hardcoded the user data. Everything personal lives in:

- `rules/email/taxonomy.md` — label set + inbox-stays/archive rules
- `rules/email/accounts.md` — Gmail accounts, per-sender rules, promo domains
- `rules/hooks/*.md` — per-hook config

A future `community_rules/email-*.md` will ship redacted installable templates.

## Not in v1

- Live `iga-gmail` MCP client (currently stubbed; `IGA_EMAIL_MOCK=1` for tests)
- Auto-archive logic beyond day-1
- Snooze handling
- Notion mirror (Vault DB not built)
- Auto-trigger hooks (manual `--run-hooks` only)
- `/gm`, `/back` integration (those still call MCP directly per
  `rules/commands.md` — this engine is a separate, deeper triage pass)

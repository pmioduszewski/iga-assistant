---
name: iga-proactive-research
description: Autonomous research notes prepared in the background for upcoming tagged tasks
prerequisites:
  - name: todoist-api-token
    description: Required for Todoist-driven research triggers (label `iga-research`). Without it, only MemPalace-flag triggers fire.
    check: any(env(TODOIST_API_TOKEN), file(~/.config/todoist/token, mode=0600))
    guide: docs/setup-todoist-token.md
    severity: warning
  - name: mempalace-server
    description: Required for filing research drawers and reading flag triggers.
    check: mcp(GaiaMemory)
    severity: error
  - name: launchd-agent-loaded
    description: Phase 2 — twice-daily auto-fire requires the LaunchAgent com.iga.research-scanner to be loaded.
    check: launchctl list 2>/dev/null | grep -q com.iga.research-scanner
    guide: engine/launchd/install.sh
    severity: warning
  - name: launchd-wrapper-executable
    description: Phase 2 — wrapper script must be executable (install.sh sets this).
    check: file(engine/launchd/iga-research-scanner, mode=0755)
    guide: engine/launchd/install.sh
    severity: warning
  - name: pmset-wake-scheduled
    description: Phase 2 — macOS must wake before 07:00 for the LaunchAgent to fire (shared with iga-email).
    check: pmset -g sched 2>/dev/null | grep -qiE "wake|poweron"
    guide: engine/launchd/install.sh
    severity: info
triggers:
  - kind: hook
    spec: rules/commands.md /gm step 1a — scanner fires inline, spawns up to 3 background subagents
  - kind: hook
    spec: rules/commands.md /back step 1a — scanner fires inline, spawns up to 2 background subagents
  - kind: scheduled
    spec: launchd LaunchAgent com.iga.research-scanner — 07:00 + 19:00 Europe/Warsaw, spawns headless `claude -p` workers via daemon mode
mempalace_wings:
  - projects/*/research
  - gaia/tooling/iga-research-meter
  - "*/research-queue"
mcp_dependencies:
  - GaiaMemory
  - claude_ai_Todoist
status: shipped
---

# Iga — Proactive Research Hook

Iga autonomously prepares background research notes for upcoming tasks. The user arrives at the slot with the briefing already filed; no manual "go research X" required.

**Status (2026-05-14):** spec v2 — pull-based design, no external cron required. Scanner runs inline during `/gm` and `/back`. Workers spawn as in-session subagents. Phase 2 (optional launchd installer for true background scheduling) is documented but not required for v1.

## Architecture

Three layers, decoupled:

1. **Scanner (inline, fires from /gm and /back)** — detects candidates from Todoist + MemPalace flags, dedupes against existing research, writes a work queue, spawns workers in background.
2. **Workers (in-session subagents)** — do the research, write outputs.
3. **Surfacer (inline in /gm + /back)** — reads recent research drawers, shows TL;DRs.

## Layer 1 — Scanner

**When:** triggered inline at the start of `/gm` and `/back`. The scanner runs synchronously to compute the candidate queue (~1–2 sec), then **spawns workers asynchronously as background subagents**. The /gm or /back response continues immediately; worker outputs land over the next 0–10 minutes.

Phase 2 (optional, not required for v1): a launchd LaunchAgent (Mac) or systemd timer (Linux) can fire the same scanner script at fixed times even when Claude Code isn't open. Same scanner script, different entrypoint. Installer ships as a Gaia skill subcommand (`/gaia install proactive-research`).

**Triggers (any fires a candidate):**

1. **Todoist label `iga-research`** — explicit opt-in. Any open task with this label whose `dueDate` (or `deadlineDate` if no due) falls within the next 7 days is a candidate. Task's due date carries the temporal signal — no separate calendar trigger needed.

2. **MemPalace flag drawers** — drawers in any wing with `room: research-queue` and metadata `triggered: false`. When the user says "research X before Y" Iga files one of these. Scanner picks them up and flips `triggered: true` after queuing.

**Dropped from v1:** calendar keyword scan. Rationale (2026-05-14 decision): Todoist tasks already carry due dates, and tagging is cheap. Calendar would mostly duplicate Todoist signals with worse precision. Future v2 may revisit.

**Dedup (idempotency):** for each candidate, compute `topic_hash = sha1(normalized_title + target_date).hexdigest()[:16]`. Normalize title: lowercase, strip whitespace, collapse internal whitespace, drop emoji and punctuation. Query MemPalace for a drawer matching `RESEARCH:<topic_hash>` in `projects/*/research`; skip if any have `last_updated > NOW() - 48h`. If MemPalace lacks `last_updated`, fall back to name-only dedup (conservative — never re-researches until drawer is deleted).

**Output:** `~/Gaia/scratch/iga-research-queue.json` — array of:
```json
{
  "topic_hash": "...",
  "source": "todoist|mempalace",
  "source_id": "...",
  "title": "...",
  "context": "3 sentences pulled from source",
  "target_date": "2026-05-18",
  "depth": "shallow|deep",
  "spawned_at": null,
  "completed_at": null
}
```

**Hard caps:**
- Max 3 candidates spawned per tick (rest stay queued for the next /gm or /back)
- If queue.length > 10 → alert the user via the next /gm or /back surfacing block, pause spawning until cleared

## Layer 2 — Workers

**Spawn model (v1, inline mode):** scanner emits a `WORKER_REQUEST` for each queued item. The calling Claude Code session reads requests after the scanner returns and dispatches them via the `Agent` tool with `run_in_background: true` and `subagent_type: general-purpose`. Each subagent gets the queue entry as prompt context.

**Phase 2 (daemon mode):** when fired by launchd/systemd outside of an interactive Claude session, scanner shells out to `claude --bare -p ... --session-id iga-research-<hash>` sequentially. Switch via env `IGA_RUN_MODE=inline|daemon` (default: `inline`).

**Model selection:** Sonnet for `depth: shallow`, Opus for `depth: deep`. Default shallow. Scanner marks deep if context matches keywords: trademark, legal, security incident, competitive recon, finance forecast, contract review.

**Budget per worker:** 30 minutes wall-clock max. If exceeded → kill, file partial output with `status: timeout`.

**Worker capabilities:**
- ✅ WebSearch, WebFetch
- ✅ MemPalace search + add_drawer (read everything, write only to `projects/<project>/research/`)
- ✅ Linear search, Jira search, Slack search (read-only)
- ❌ No code edits, no shell commands beyond read-only, no Todoist writes except the one comment on the source task
- ❌ No external API calls that cost money or send messages

**Output contract (mandatory):**

1. **MemPalace drawer** at `wing: projects/<inferred_project>`, `room: research`, content in AAAK:
   ```
   RESEARCH:<topic_hash>|<target_date>|depth:<shallow|deep>|★★★
   TLDR: <one sentence>
   FINDINGS:
   - <fact>
   - <fact>
   SOURCES: <urls>
   RECOMMENDATIONS: <2-3 bullets>
   CONFIDENCE: <low|med|high>
   ```

2. **Todoist comment on source task** (only if `source: todoist`): the TL;DR + MemPalace drawer ID. Skip if `source: mempalace` (no Todoist task to comment on).

3. **Queue update:** mark `spawned_at` + `completed_at` on the queue entry. Scanner reads on next tick to skip.

## Layer 3 — Surfacing

**In `/gm` and `/back`:**

1. **Before** any other heavy work, fire the scanner (Layer 1). It returns the candidate queue and emits worker spawn requests.
2. /gm or /back dispatches the spawns as **background subagents** and continues immediately — no waiting.
3. **After** all other steps (calendar, Todoist, email, etc.), the surfacing step reads MemPalace `wing: projects/*, room: research` drawers created since the last diary entry (`mempalace_diary_read agent_name=gaia last_n=1`).
4. For each: show one line: `📑 <project>: <TL;DR>` + drawer ID. Silent if 0. If > 3 → top 3 by `target_date` ascending, mention "+N more".
5. If worker spawns from step 1 are still running, surface section says `📑 Iga prepared: <N done> + <K running>` — the user will see the rest at next /back or /gm.

Surfaced section header: `📑 Iga prepared in the background:`

## Config

Tunables live in this file (not hardcoded):

- `scanner.lookahead_days`: `7`
- `scanner.dedup_window_hours`: `48`
- `scanner.max_spawn_per_tick`: `3`
- `scanner.queue_alert_threshold`: `10`
- `scanner.run_mode_default`: `inline` (`inline` for /gm-triggered, `daemon` for launchd)
- `worker.budget_minutes`: `30`
- `worker.deep_keywords`: `[trademark, legal, security incident, competitive recon, finance forecast, contract review]`
- `triggers.todoist_label`: `iga-research`
- `triggers.mempalace_room`: `research-queue`

## Killswitches

- `IGA_PROACTIVE_RESEARCH=0` in env → scanner exits early, files no queue
- `IGA_PROACTIVE_SPAWN=0` → scanner runs detection but doesn't spawn workers (useful for debugging detection without burning tokens)
- Manual: comment out the scanner step in `rules/commands.md` /gm and /back

## Cost guardrails

- Track `worker_invocations_per_week` in MemPalace `gaia/tooling/iga-research-meter` (one drawer per ISO week, append timestamps)
- If > 20 invocations in a week without the user `/gm`-acknowledging at least 5 outputs → auto-pause, surface alert next /gm
- Reset acknowledgment counter each /gm where the user doesn't immediately archive the research section

## Prerequisites

Declared in this file's frontmatter and picked up by `/gaia status` generic prereq scan. See `CLAUDE.md` → `/gaia status` for the schema. Summary:

- **`todoist-api-token`** (severity: warning) — Todoist-triggered research won't fire without it; MemPalace-flag triggers continue working. Guide: [`docs/setup-todoist-token.md`](docs/setup-todoist-token.md).

## Installation (OSS-friendly, zero-touch)

**v1 (default):** nothing to install. Iga's `rules/commands.md` already includes the scanner-fire step. First /gm or /back after rules sync activates the system. If `~/.config/todoist/token` isn't set, the scanner exits cleanly (no error spam) and only MemPalace-flag triggers fire — Todoist triggers wake up the moment a token is provided.

**Phase 2 (optional, Mac/Linux) — `/gaia install proactive-research`:** writes the platform-appropriate scheduler. Follows the launchd/systemd contract from `skills/create-iga-skill/SKILL.md` § "Scheduled / background skills".

Mac layout (when Phase 2 ships):
```
skills/iga-proactive-research/engine/launchd/
  com.iga.proactive-research.plist     ← LaunchAgent template
  iga-research-scanner                 ← named zsh wrapper (NOT scanner.sh — must surface descriptively in Login Items)
  install.sh                           ← idempotent installer
  uninstall.sh                         ← clean removal
docs/setup-launchd.md                  ← step-by-step + pmset wake guide
```

The wrapper script exports `PATH="$HOME/.volta/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"` so the embedded `claude --bare -p` daemon-mode worker spawns can find node/pnpm/claude. Per spec contract, no `/bin/zsh -lc "..."` in the plist.

**Phase 2 is BLOCKED on a hardware-habit research task** — see "Open question — Mac power-off" below.

Linux: `~/.config/systemd/user/iga-research.timer` + `.service`, `systemctl --user enable --now iga-research.timer`. Same wrapper + same PATH setup.

Windows: not supported (use v1 only).

## ⚠️ Open question — Mac power-off habit (blocks Phase 2)

**Discovery:** if the user fully powers off the machine at night (rather than sleeping it), the standard launchd + `pmset repeat wake` pattern breaks, because `pmset repeat wake` only wakes from sleep, not from a powered-off state.

Implications for this skill:
- **Evening fire** (machine still on): ✅ works as-is
- **Morning fire** (machine off): ❌ **at risk** — won't fire until manual power-on

Candidate workarounds being investigated:

1. `pmset repeat wakeorpoweron` on M2 Apple Silicon — unreliable per community reports, needs 2–3 overnight test attempts
2. Smart plug + `pmset -a autorestart 1` (already set per current `/gaia status`) — needs power-cut test
3. macOS Shortcuts triggered at login — depends on auto-login
4. **LaunchAgent with `RunAtLoad=true` + dated lock file** — fires the moment Mac powers on, lock file prevents double-fire within the same target window. Independent of when power-on actually happens.

**Likely recommendation for this skill:** option 4. The morning brief lands "the moment the user powers on the machine" rather than at an exact wall-clock time. Pre-wake from powered-off is currently not solved.

## Open implementation tasks

See Todoist label `iga-research-impl`:

| Task | State |
|---|---|
| Scanner script `engine/scanner.py` (calendar dropped, IGA_RUN_MODE switching, WORKER_REQUEST stdout) | ✅ shipped 2026-05-14, 18/18 tests green |
| Worker prompt `engine/worker.prompt.md` | ✅ shipped 2026-05-14 |
| `/gm` + `/back` scanner-fire + surfacing wiring (`rules/commands.md` step 1a + step 8/5) | ✅ shipped 2026-05-14 |
| Todoist token setup guide `docs/setup-todoist-token.md` + `/gaia status` prereq scan | ✅ shipped 2026-05-14 |
| Migration to `skills/<name>/` layout (from `rules/` + `scripts/` + `tests/`) | ✅ shipped 2026-05-14 |
| **E2E test: tag a real task with `iga-research`, run /gm, verify drawer + comment** | ⏳ pending — first live trigger validation |
| **Phase 2 launchd installer** | ⏳ blocked on wake-from-off research |

## Phase 2 prereqs (will activate in frontmatter when installer ships)

Per the launchd contract in `skills/create-iga-skill/SKILL.md`, when Phase 2 lands the frontmatter will gain three additional prereqs:

```yaml
- name: launchd-agent-loaded
  description: LaunchAgent must be loaded for scheduled morning/evening fires
  check: launchctl list 2>/dev/null | grep -q com.iga.proactive-research
  guide: docs/setup-launchd.md
  severity: warning

- name: launchd-wrapper-executable
  description: Wrapper script must be executable (install.sh sets this; sanity check)
  check: file(engine/launchd/iga-research-scanner, mode=0755)
  guide: docs/setup-launchd.md
  severity: warning

- name: wake-from-off-strategy
  description: Mac must wake/power-on before scheduled fire (the user powers off nightly — pmset wake alone is insufficient)
  check: any(pmset -g sched 2>/dev/null | grep -qiE "wake|poweron", file(engine/launchd/iga-research-scanner.lock-strategy, mode=0644))
  guide: docs/setup-launchd.md#wake-from-off
  severity: info
```

(Deliberately NOT activated now — they would surface false-positive warnings on a v1-only install. They land alongside `engine/launchd/` in Phase 2.)

## Future extensions (not v1)

- Slack DM channel for research-ready notifications instead of waiting for /gm
- Calendar trigger (revisit if Todoist coverage proves insufficient)
- Auto-pull from Jira sprint planning when a sprint starts
- Project-specific research templates (e.g. `<your-project-A>` = competitive recon, `<your-project-B>` = customer demo prep)
- Self-learning: track which research outputs the user actually used vs ignored, tune triggers accordingly

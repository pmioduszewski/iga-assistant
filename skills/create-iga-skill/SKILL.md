---
name: create-iga-skill
description: Meta-skill — scaffold a new Iga skill consistently when the user expresses skill-creation intent
intent_triggers:
  - "I want a skill that"
  - "I'd like a skill"
  - "create a skill"
  - "build a skill"
  - "make a skill"
  - "new skill"
  - "let's build an iga skill"
  - "let's build a iga skill"
  - "wouldn't it be cool if iga could"
  - "wouldn't it be cool if iga could"
  - "can iga learn to"
  - "can iga learn to"
  - "iga should track"
  - "iga should track"
  - "/new-skill"
triggers:
  - kind: auto
    spec: matches any intent_triggers pattern in a user message
  - kind: slash-command
    spec: "/new-skill <description>"
mempalace_wings:
  - iga/architecture/skills-inventory
status: shipped
---

# Create Iga Skill — meta-skill template

When the user says "I want a skill that does X" (or invokes `/new-skill <description>`), Iga follows this template to scaffold a new skill consistently. The output is a working capture pattern + a queued build task, NOT a fully-coded engine. Engine code ships later in a dedicated coding session.

## Why this exists

By 2026-05-14 the user has 8+ skill ideas in flight (Knowledge Vault, Quote, Scripture, Personal Trainer, Proactive Research, Newsletter Sweep, Domain Inventory, Subscriptions Audit, Runaway Bill Monitor, etc.). Each one was designed ad-hoc. This meta-skill standardizes the pattern so:
1. The user gets consistent scaffolding (no decision fatigue per skill)
2. Skills become installable community packs (`iga install <skill>`) for OSS reuse
3. Future Iga can identify which design choices matter vs which are aesthetic

## The 5-step template Iga follows

### Step 1: Brief proposed architecture

Reading the user's request, Iga drafts a 1-paragraph + bulleted summary covering:

- **Storage:** which MemPalace wing/room? New or existing?
- **Notion mirror:** yes/no? if yes, schema
- **Capture surface:** slash command? auto-trigger? both?
- **Surfacing rule:** when does Iga share/inject output? frequency cap? context filter?
- **Tonality:** contemplative / direct / Hormozi / neutral / user-specific
- **OSS separation:** what goes in `skills/<name>/engine/` (engine code) vs `skills/<name>/SKILL.md` (LLM spec + user config) vs `skills/<name>/SKILL.local.md` (user-personal overrides, gitignored) vs `community_skills/<name>/` (installable redacted template)
- **Trigger conditions** if surfacing autonomously
- **Adherence/usage tracking** if relevant (e.g. trainer logs sessions; quote tracks last-surfaced date)

Keep brief tight — 100-200 words. Don't pre-decide; surface trade-offs.

### Step 2: AskUserQuestion — 1-3 closed decisions

Surface only the decisions that genuinely need the user's input. Examples:

- **Storage location:** new wing vs extend existing wing
- **Surfacing aggression:** silent ( the user queries) / passive (surface in /gm only) / proactive (Iga injects during conversations)
- **Notion mirror:** yes / no / defer until first 10 entries
- **Tonality:** match an existing precedent (Trainer = Hormozi, Scripture = contemplative) or define new

Default to recommended option in each. The user can `Other`.

### Step 3: On approval — scaffold

Concretely:

1. **Write `skills/<skill-name>/SKILL.md`** with the user's config using the template in Step 4 (create the skill dir + any `engine/`, `tests/`, `docs/` subdirs as needed)
2. **Init MemPalace wing** if new — file a seed drawer at `<wing>/<room>` explaining what the wing stores
3. **Add Todoist build task** with full engine spec in description (so the future user + coding session can pick up without re-designing)
4. **Optionally** create Notion DB template (when Knowledge Vault Notion infra is shipped)
5. **Register skill** in MemPalace `iga/architecture` skills-inventory drawer (one source of truth for which skills exist + status)
6. **Tell the user:** "Scaffolding done. Build task `<id>` queued. Capture via `<slash command>` works now — Iga will file manually to MemPalace until engine ships."

### Step 4: `skills/<skill-name>/SKILL.md` template

Use this as the file content scaffold. **The frontmatter is mandatory** — generic Iga commands (e.g. `/iga status`, future `/iga list-triggers`, `/iga doctor`) scan it. A skill without frontmatter is invisible to those commands.

````markdown
---
name: <skill-slug>                         # MUST match the skill directory name
description: <one line, what this does>
# OPTIONAL — declare only the fields that apply to this skill:
intent_triggers:                           # natural-language phrases that auto-invoke this skill (see CLAUDE.md Behavioral Hooks)
  - <phrase or pattern, case-insensitive substring match>
  - <e.g. "log my mood", "track my caffeine", "I want to remember">
prerequisites:                             # picked up by /iga status prereq scan
  - name: <prereq-slug>
    description: <one line, why needed>
    check: <DSL clause — see CLAUDE.md /iga status>
    guide: <path relative to the skill dir, e.g. docs/setup-X.md>
    severity: warning | error | info       # default: warning
triggers:                                  # picked up by future /iga list-triggers
  - kind: slash-command | auto | hook | scheduled
    spec: <pattern, e.g. "/quote <text>" or "on /gm step N">
mempalace_wings:                           # which wings this skill writes to
  - <wing>/<room>
mcp_dependencies:                          # which MCP servers this skill requires
  - <mcp-name>
status: scaffolded | building | shipped | community-packed
---

# <Skill Name> Rules

Iga's <skill-name> skill config. Generic engine in `skills/<skill-name>/` (forthcoming — Todoist `<build-task-id>`). This file is user-personal — config layer only.

## Purpose
<one paragraph: what problem this solves for the user>

## Capture
- Slash command: `/<cmd> <args>`
- Auto-trigger: <conditions if any, else "none">

## Storage
- MemPalace wing: `<wing>/<room>` — one drawer per item
- Schema per drawer: <field list>
- Notion mirror: <yes/no, schema if yes>

## Surfacing rules
- <when Iga surfaces output: /gm, /back, inline conversation, on-demand>
- Frequency cap: <e.g. once per day, max once per long conversation>
- Context filter: <when to skip, e.g. during burnout-spiral, during deep work>
- Tonality: <Hormozi / contemplative / direct / etc.>

## user-specific config
- <preferences, lists, schedules, equipment, etc.>

## Adherence/usage tracking
- <if relevant, e.g. last-surfaced date, completion log, streak>

## Open questions
- <items to resolve as skill matures>

## Connects to
- <other MemPalace drawers, rules files, Todoist tasks>

## OSS-clean separation
- `skills/<name>/engine/`: <what's generic — engine code, no user data>
- `skills/<name>/SKILL.md` (this file): LLM spec + shared config
- `skills/<name>/SKILL.local.md`: user-personal overrides (gitignored)
- `community_skills/<name>/` (future): redacted installable template
````

### Step 5: Iga can capture immediately — no waiting on engine

Critical: capture pattern works the day scaffolding is done. Iga manually:
- Receives items via the slash command pattern (`/quote <text> — <attr>`, `/verse Psalm 23`, `/move`, etc.)
- Files to MemPalace via `mempalace_add_drawer`
- Surfaces per the `skills/<name>/SKILL.md` config

The engine code in `skills/<name>/engine/` ships later — it automates what Iga is already doing manually. But the user doesn't have to wait to start using the skill.

## Skills inventory (lives in MemPalace `iga/architecture/skills-inventory`)

Each skill registered there has: name, status (scaffolded / building / shipped / community-packed), `skills/<name>/SKILL.md` path, Todoist build task ID, MemPalace wings used, last-updated date.

## On invoking `/new-skill <description>`

The user types e.g. `/new-skill track my caffeine intake and tell me when I've had too much for the day`. Iga:
1. Reads this file
2. Executes Step 1 brief (what wing, what command, what surfacing)
3. AskUserQuestion (Step 2)
4. On approval: scaffolds (Step 3) + writes `skills/caffeine/SKILL.md` + queues build task
5. Capture works immediately: `/caffeine 1 coffee` adds drawer to `user/health/caffeine-log`

## What this skill does NOT do

- Does not implement engine code — that's the build task
- Does not over-engineer — minimum viable scaffolding only
- Does not bypass the user's consent — every step is approval-gated via AskUserQuestion
- Does not skip the SKILL.md template — consistency is the whole point
- Does not skip the frontmatter — generic commands depend on it (see below)

## ⚠️ BINDING — Generic command compatibility (the composability contract)

**Iga must NEVER hardcode skill-specific names, prereqs, triggers, or config into generic commands** like `/iga status`, `/iga install`, `/iga rules`, `/gm`, `/back`, etc. Generic commands are thin layers that scan frontmatter and config files; specifics live IN the skill's own files.

**Concrete contract:**

| If a generic command needs to know X about a skill | The skill declares X here |
|---|---|
| Whether prereqs are satisfied | `prerequisites:` in SKILL.md frontmatter |
| What triggers fire it | `triggers:` in SKILL.md frontmatter |
| Where it writes memory | `mempalace_wings:` in SKILL.md frontmatter |
| Required MCP servers | `mcp_dependencies:` in SKILL.md frontmatter |
| Build / install / shipped status | `status:` in SKILL.md frontmatter |

**Anti-pattern (do NOT do this):**

```markdown
# In CLAUDE.md
/iga status — ... step 5: check if iga-proactive-research has a Todoist token...
```

That couples a generic command to a specific skill. Next time the skill renames, or a new skill needs the same check, the generic command rots. **It also defeats OSS reuse** — community installers of one skill shouldn't carry references to skills they don't have.

**Correct pattern:**

```markdown
# In CLAUDE.md
/iga status — ... step 5: read every rules/*.md and skills/*/SKILL.md frontmatter; for each prerequisites: entry, evaluate the check: clause and surface unsatisfied ones.

# In skills/iga-proactive-research/SKILL.md frontmatter
prerequisites:
  - name: todoist-api-token
    check: any(env(TODOIST_API_TOKEN), file(~/.config/todoist/token, mode=0600))
    guide: docs/setup-todoist-token.md
```

Generic command has zero pack-specific knowledge. Adding a new skill with prereqs requires editing nothing outside that skill.

**Past incident (2026-05-14):** Iga drafted `/iga status` with a hard-coded `Prereq registry` block naming `iga-proactive-research → Todoist token`. The user caught it: *"/iga status should be generic thin layer that guide to check skills / rules and pick things from there since Iga / Iga should be composable & generic. Did you just hardcoded proactive research things into this generic command?"* — Yes. Fixed by moving the prereq declaration into the skill's own frontmatter and rewriting `/iga status` to walk frontmatter generically. **This rule exists so future Iga doesn't repeat the mistake.**

## How to verify a skill is composability-compliant

Before marking a skill `shipped`, run this self-check:

1. Does the rule file have all the relevant frontmatter fields? (`name`, `description`, plus any optional ones that apply)
2. Are all of this skill's prereqs declared in `prerequisites:`? (i.e., `/iga status` would catch them without needing edits elsewhere)
3. Are this skill's triggers declared in `triggers:`? (so a future `/iga list-triggers` would find them)
4. If this skill should auto-fire on natural-language intent, are the phrases declared in `intent_triggers:`? ( the user shouldn't need to remember the skill's name to invoke it)
5. Is `mempalace_wings:` populated for any wings this skill writes to?
6. Does CLAUDE.md or any other generic file mention this skill by name? **If yes, that's a coupling smell — investigate whether it should move into frontmatter.**
7. If this skill needs config the user provides interactively (tokens, paths, secrets), does the prereq `guide:` field point at a setup doc?
8. **If this skill is meant for OSS distribution, has the engine been cleaned of user-specific references and personal config moved to a `.local.md` companion?** (See "OSS publication path" below.) Even if not yet OSS-published, separating early prevents pain later.

If any answer is no, fix before shipping.

## Discoverability — why `intent_triggers:` matters

The user shouldn't have to remember that a skill exists in order to invoke it. The `intent_triggers:` field lists natural-language phrases that auto-invoke the skill via CLAUDE.md's behavioral hook. Example: this meta-skill declares triggers like "I want a skill that", "create a skill", "wouldn't it be cool if iga could" — so the user saying any of those auto-loads this template, no `/new-skill` typing required.

Skills with no natural-language entry point (purely scheduled, or only fired by other skills) can omit `intent_triggers:` entirely.

## OSS publication path — splitting generic from personal

A skill goes through these stages as it matures:

| Stage | Files | Notes |
|---|---|---|
| **Scaffolded (private)** | `skills/<skill>/SKILL.md` only | New skill from this meta-skill. Lives in user's local tree. May contain user-personal bits mixed with engine logic. |
| **Engine extracted** | `skills/<skill>/SKILL.md` (engine only) + `skills/<skill>/SKILL.local.md` (personal) | Personal config moved out — names, lists, emails, tonality preferences, etc. |
| **Community-packed** | `community_skills/<skill>/SKILL.md` (generic engine, ships to GitHub) + `skills/<skill>/SKILL.md` (installed copy w/ provenance) + `skills/<skill>/SKILL.local.md` (personal) | Ready for OSS reuse. Other users can `iga install <skill>` and add their own `SKILL.local.md`. |

**When extracting personal bits from a scaffolded skill, ask:**

- Would another user need to change this? → It's personal. Move to `.local.md`.
- Does this reference the user by name, mention specific people they know, list specific email accounts/projects, or hard-code particular businesses/collaborators by name? → Personal. `.local.md`.
- Is this a generic capability that anyone with this skill would want? → Engine. Stays in main rule file.
- Is this a config knob with a reasonable default? → Default goes in engine; override capability goes in `.local.md`.

**`.local.md` minimal frontmatter:**

```yaml
---
extends: <pack-name>                       # matches the engine file's `name:`
description: <one-line, what's customized>
---
```

Body of `.local.md` can be free-form. Iga reads the engine rule first, then the `.local.md` on top — local always wins on conflicts.

**Anti-patterns when authoring a `community_skills/<pack>/SKILL.md` (or `community_rules/<pack>.md`):**

- ❌ Mentioning the user by real name in engine body or frontmatter
- ❌ Listing specific email accounts, project names, business names
- ❌ Hardcoding local file paths beyond `~/.config/`, `~/Gaia/`, repo-relative paths
- ❌ Referring to OTHER skills by their `name:` slug (couples packs together) — describe the integration in prose so users with different skill choices aren't broken
- ✅ Use placeholders like `<your email>` or document override surface in a `## Override` section pointing at `.local.md`

This contract is **what makes iga-assistant a real OSS project** — without it, `iga update` would be unsafe to ever run.

## Scheduled / background skills (launchd, cron, systemd)

Some skills need to run autonomously — daily, hourly, or at fixed times. Examples: morning email triage, twice-daily proactive-research scanner, weekly subscription audit.

### Decision: where does the schedule live?

Two distinct mechanisms, NOT interchangeable:

| Mechanism | Runs where | Can access local MCPs | Can access MemPalace | Can spawn local `claude --bare -p` | Use when |
|---|---|---|---|---|---|
| **macOS `launchd` LaunchAgent** | The user's Mac | ✅ Yes | ✅ Yes (it's a local SQLite file) | ✅ Yes | The skill touches local resources (Iga MCPs, MemPalace, local credentials, local file writes) |
| **Linux `systemd` timer** | The user's Linux box | ✅ Yes | ✅ Yes | ✅ Yes | Same as launchd, Linux equivalent |
| **`cron` (BSD/Linux)** | The user's machine | ✅ Yes | ✅ Yes | ✅ Yes | Simpler but less reliable (doesn't catch up after sleep on macOS; replaced by launchd on modern macOS) |
| **Anthropic Claude Code `/schedule` (cloud cron via `CronCreate`)** | Anthropic's cloud sandbox | ❌ NO | ❌ NO | ❌ NO | The work runs entirely against claude.ai-hosted MCPs (Todoist, Linear, Slack, Notion, GCal) and produces output that lands back in those tools. NEVER for skills that need local Iga MCPs or MemPalace. |

**Past mistake (2026-05-14):** Iga initially suggested using `/schedule` for `iga-email triage`. Wrong — the `iga-email` MCP is a local stdio process. Cloud cron can't reach it. Same blocker affected `iga-proactive-research` (its scanner needs MemPalace writes + local `claude --bare -p` worker spawns). **Rule:** if the skill writes to MemPalace, spawns local Claude workers, reads from `~/.local/share/`, or invokes a local stdio MCP, use launchd/systemd. Claude Code's `/schedule` is only for fully-cloud workflows.

### macOS launchd skill template

For a scheduled skill targeting macOS, the skill ships:

```
skills/<name>/
  engine/launchd/
    com.iga.<name>.plist          ← LaunchAgent template (referenced paths use placeholders)
    iga-<name>-<verb>             ← named zsh wrapper invoked by the plist (e.g. iga-email-triage, iga-research-scan)
    install.sh                    ← idempotent installer: copies plist to ~/Library/LaunchAgents/, runs launchctl load
    uninstall.sh                  ← launchctl unload + rm
  docs/
    setup-launchd.md              ← step-by-step for the user (or any user)
```

### CRITICAL: name the wrapper script meaningfully

The wrapper script's filename **surfaces in macOS Settings → General → Login Items & Extensions**. macOS shows the program path verbatim; if you point the plist at `/bin/zsh -lc "..."` directly, macOS labels the entry "zsh from unidentified developer" — alarming for non-technical users and uninformative for everyone.

**Always use a named wrapper script:**

- ❌ `ProgramArguments` = `[/bin/zsh, -lc, "cd ... && pnpm ..."]` → Login Items shows "zsh"
- ❌ Wrapper file named `triage.sh` → Login Items shows "triage.sh" (better, still vague)
- ✅ Wrapper file named `iga-email-triage` or `iga-research-scanner` → Login Items shows the descriptive name from the skill's path

**Naming convention:** `iga-<skill-slug>-<verb>` (no extension), matches the LaunchAgent Label `com.iga.<skill-slug>` so users see the relationship.

**Why no `.sh` extension:** macOS doesn't need it (file is executable via shebang), and dropping it makes the entry read like a proper command instead of a script.

### Wrapper script skeleton

`engine/launchd/iga-<name>-<verb>` (executable, no extension):

```sh
#!/bin/zsh
# Short description of what this scheduled run does.
set -e

SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$HOME/Library/Logs/iga"

# Volta + Claude CLI commonly install outside the default launchd PATH.
# Prepending these makes node, pnpm, and `claude` discoverable.
export PATH="$HOME/.volta/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

mkdir -p "$LOG_DIR"
cd "$SKILL_DIR"

# The actual work — keep it one exec so signals propagate cleanly.
exec <command that produces JSON to stdout>
```

Two PATH dirs worth always including for the user's setup (and most Volta + Claude Code users):

- `$HOME/.volta/bin` — Volta-managed node/pnpm/tsx
- `$HOME/.local/bin` — Claude Code CLI installer destination

### Skeleton plist

**Skeleton plist** (`engine/launchd/com.iga.<name>.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.iga.<name></string>

  <key>ProgramArguments</key>
  <array>
    <string><SKILL_DIR>/engine/launchd/iga-<name>-<verb></string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>6</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>

  <key>StandardOutPath</key>
  <string><HOME>/Library/Logs/iga/<name>.log</string>

  <key>StandardErrorPath</key>
  <string><HOME>/Library/Logs/iga/<name>.err.log</string>

  <key>RunAtLoad</key>
  <false/>

  <key>WorkingDirectory</key>
  <string><SKILL_DIR></string>
</dict>
</plist>
```

Key notes:
- `ProgramArguments` points at the **named wrapper script**, not `/bin/zsh -lc`. macOS Login Items will surface the wrapper's filename — make it descriptive (`iga-<name>-<verb>`).
- The wrapper script handles PATH setup, cd, and any pre-flight, so the plist stays minimal.
- `StandardOutPath` + `StandardErrorPath` give per-skill log files under `~/Library/Logs/iga/` — easy to grep, doesn't pollute syslog.
- `RunAtLoad=false` means it only fires on schedule, not when loaded. Set `true` for one-shot test.
- `<SKILL_DIR>` and `<HOME>` placeholders get substituted by `install.sh`.

### Waking the Mac before the schedule fires

macOS does NOT wake the machine just because a launchd job is scheduled. If the Mac is asleep at the trigger time, the job runs **on next wake** (which may be hours later — defeats the purpose).

**To pre-wake the Mac:**

```bash
sudo pmset repeat wake MTWRFSU 05:55:00
```

This schedules a daily wake-from-sleep at 05:55 (5 min before the 06:00 job). Caveats:

| Constraint | Required for wake to work |
|---|---|
| Power | **Must be plugged in.** macOS does not wake from sleep on battery to run pmset events. |
| Lid (laptops) | **Open**, OR clamshell-mode (closed + external display + power + bluetooth keyboard/mouse paired) |
| Login session | **User logged in before sleep.** LaunchAgents run in the user's context. If logged out, the job won't run until next login. Set auto-login OR just never log out ( the user's pattern) |
| Always-plugged Mac mini / desktop | All above caveats moot. Mac minis on the user's setup are the ideal target. |
| Major macOS updates | Sometimes reset `pmset repeat` schedules. Re-run after Sequoia → Sonoma → ... upgrades |

Verify wake schedule: `pmset -g sched`

### Installer pattern

`engine/launchd/install.sh`:

```sh
#!/bin/sh
set -e
SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SKILL_NAME="<name>"
PLIST_SRC="$SKILL_DIR/engine/launchd/com.iga.$SKILL_NAME.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.iga.$SKILL_NAME.plist"

mkdir -p "$HOME/Library/Logs/iga"
mkdir -p "$HOME/Library/LaunchAgents"

# Substitute placeholders
sed -e "s|<SKILL_DIR>|$SKILL_DIR|g" \
    -e "s|<HOME>|$HOME|g" \
    -e "s|<COMMAND>|<the actual command>|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Unload if already loaded (idempotent re-install)
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "Installed and loaded: com.iga.$SKILL_NAME"
echo "Verify: launchctl list | grep com.iga.$SKILL_NAME"
echo "Logs: ~/Library/Logs/iga/$SKILL_NAME.log"
```

### Manual test before relying on it

After installing the LaunchAgent, FORCE one run to verify everything works end-to-end:

```bash
launchctl start com.iga.<name>
# Wait a few seconds, then:
tail -50 ~/Library/Logs/iga/<name>.log
tail -50 ~/Library/Logs/iga/<name>.err.log
```

If errors mention missing binaries (`pnpm: command not found`, `node: command not found`), the PATH inside the LaunchAgent isn't right — fix via `EnvironmentVariables` or fully-qualified paths in `ProgramArguments`.

### Frontmatter declaration (so /iga status catches install drift)

Scheduled skills declare the trigger so `/iga list-triggers` (future) can show them:

```yaml
triggers:
  - kind: scheduled
    spec: "launchd LaunchAgent com.iga.<name> — daily 06:00 Europe/Warsaw (see engine/launchd/)"
```

And declare prerequisites so `/iga status` warns and guides the user when the schedule isn't fully wired. Three prereqs cover the canonical install:

```yaml
prerequisites:
  - name: launchd-agent-loaded
    description: The LaunchAgent must be loaded for the scheduled run to fire
    check: launchctl list 2>/dev/null | grep -q com.iga.<name>
    guide: docs/setup-launchd.md
    severity: warning

  - name: launchd-wrapper-executable
    description: The wrapper script must be executable (install.sh sets this; sanity check after edits)
    check: file(engine/launchd/iga-<name>-<verb>, mode=0755)
    guide: docs/setup-launchd.md
    severity: warning

  - name: pmset-wake-scheduled
    description: macOS must wake before the scheduled fire time (only relevant if the Mac sleeps)
    check: pmset -g sched 2>/dev/null | grep -qiE "wake|poweron"
    guide: docs/setup-launchd.md#pmset-wake
    severity: info
```

Severity meanings for scheduled-skill prereqs:
- `launchd-agent-loaded` → **warning**: skill still works interactively, but no autonomous fires
- `launchd-wrapper-executable` → **warning**: install.sh might not have run; trivial fix
- `pmset-wake-scheduled` → **info**: doesn't matter on always-on machines (Mac mini, plugged-in iMac); matters for laptops that sleep

`/iga status` walks frontmatter from every `rules/*.md` and `skills/*/SKILL.md`, evaluates `check:` clauses (the DSL is interpretive — `cmd()`, `file()`, `env()`, `mcp()`, or natural-language clauses Iga resolves at runtime), and surfaces unsatisfied prereqs with the `guide:` path. If any are missing, `/iga status` offers to walk the user through `docs/setup-launchd.md` interactively, doing the safe steps automatically and confirming the sudo ones.

### Guide doc structure (`docs/setup-launchd.md`)

Section headers Iga can deep-link to via `guide: docs/setup-launchd.md#<anchor>`:

- `## Prerequisites` — what must be true before installing
- `## One-time install` — run `engine/launchd/install.sh`
- `## pmset-wake` — `sudo pmset repeat wake MTWRFSU HH:MM:00`
- `## Force a test run` — `launchctl start com.iga.<name>` + tail logs
- `## Daily verification` — how to confirm last night's run fired
- `## Uninstall` — clean removal
- `## Constraints recap` — power/lid/login-session matrix

Each section should be self-contained — `/iga status` may guide the user through just one (e.g. pmset wake when the agent is loaded but wake isn't scheduled).

### When to NOT make a skill scheduled

- The skill is interactive only (e.g. trainer drill picker — the user invokes when he wants to lift)
- The skill's output is too sensitive for autonomous action (e.g. send-emails skill — always require human confirmation)
- The schedule cadence is unclear yet (start as on-demand; promote to scheduled after a few weeks of usage data)

Default: ship a skill as on-demand first. Add launchd only when there's evidence the daily/twice-daily pattern is real.

## Connects to

- CLAUDE.md `community_rules/` + `community_skills/` system (`iga install <pack>`)
- MemPalace `iga/architecture/skills-inventory` (canonical skill list)
- All existing skill tasks (Quote, Scripture, Trainer, Proactive Research, Knowledge Vault, etc.)
- `community_skills/` + `community_rules/` future contributions

Designed 2026-05-14. Scheduled-skills section added 2026-05-14 after launchd vs Claude Code `/schedule` confusion incident.

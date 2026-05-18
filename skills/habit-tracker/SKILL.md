---
name: habit-tracker
description: Generic habit-streak tracker. Reads a plain append-only log of "days a habit was done" and produces a GitHub-style contribution-grid widget plus a deterministic, no-LLM coach line (current streak / days missed). Pure stdlib producer; the app only renders the data file it emits. Deleting the app changes nothing — the producer still runs standalone.
intent_triggers:
  - habit tracker
  - habit grid
  - contribution grid
  - streak
  - habit-tracker
prerequisites:
  - name: python3
    description: The producer is stdlib-only Python 3; no third-party deps.
    check: cmd(python3)
    severity: error
  - name: state-dir-writable
    description: The producer reads ~/Gaia/state/habits/<name>.log and writes ~/Gaia/state/widgets/habit-tracker-habit-grid.json. The ~/Gaia tree must be writable.
    check: file(~/Gaia)
    severity: warning
triggers:
  - kind: cli
    spec: "`python3 skills/habit-tracker/engine/producer.py [--name NAME] [--days N]` — recompute the grid + coach line and atomically rewrite the widget data file. No daemon; run from /gm, a proactive job, or by hand."
widgets:
  - id: habit-grid
    type: contribution-grid
    title: Habit streak
    data_source: ~/Gaia/state/widgets/habit-tracker-habit-grid.json
    refresh: 60
    coach:
      tone: encouraging
      text_field: coach
substrate:
  - kind: habit-tracker
    version: 1
    path: state/substrates/habit-tracker.json
    entities: habit
    importer: engine/import_habitkit.py
    exporter: engine/export_habitkit.py
    derived:
      - widget:habit-grid
mempalace_wings:
  - iga/tooling/habit-tracker
mcp_dependencies: []
proactive: see ./proactive.yaml (the generic engine discovers skills/*/proactive.yaml; the safety gate + job contract live there and in § Proactive / Killswitch below)
status: stable
---

# Habit Tracker — contribution-grid widget

The first real widget proving the v2 **widget host contract**: a widget is a
*declarative spec + a data file*, never injected code. This skill produces the
data file; the menu-bar app renders only known widget types from it. Delete the
app and `engine/producer.py` still emits a valid widget JSON — the app is a
pure viewer.

## What it does

- Reads an append-only habits log: `~/Gaia/state/habits/<name>.log`, one ISO
  date (`YYYY-MM-DD`) per line = a day the habit was done. Blank lines and
  duplicates are tolerated.
- Computes the last ~120 days as a `contribution-grid`: one cell per day with a
  `level` 0..4 bucketed by how recently/often the habit was done in a small
  trailing window.
- Derives a **deterministic, no-LLM coach line** from the data alone (current
  streak length, or days since last done → an encouraging or gentle-nudge
  sentence).
- Emits the v1 widget data-file JSON atomically (tmp + `os.replace`) to
  `~/Gaia/state/widgets/habit-tracker-habit-grid.json`.

## Widget data-file schema (v1, shared with the app)

```
{
  "schema_version": 1,
  "widget_id": "habit-grid",
  "type": "contribution-grid",
  "title": "Habit streak",
  "generated_at": "<ISO8601 UTC>",
  "data": {
    "label": "<habit name> — <streak summary>",
    "levels": 4,
    "cells": [ { "date": "YYYY-MM-DD", "level": 0..4 }, ... ]
  },
  "coach": { "text": "<deterministic sentence>", "tone": "encouraging" } | null
}
```

The app discovers this widget by scanning this file's `widgets:` frontmatter
(read-only, same spirit as the engine's `proactive:` job discovery). A
missing / stale / garbage data file degrades to a "waiting for habit-tracker"
state in the app — it never crashes.

## Usage

```
python3 skills/habit-tracker/engine/producer.py            # default name "example"
python3 skills/habit-tracker/engine/producer.py --name reading --days 120
```

To log a day, append the date to the log (idempotent — duplicates ignored):

```
echo "$(date +%F)" >> ~/Gaia/state/habits/example.log
```

### State-root override — `IGA_STATE_DIR` (data-loss isolation)

By default the producer reads + writes the user's **live** state under
`~/Gaia/state` (habit logs in `state/habits/`, widget JSON in
`state/widgets/`). Tests, the app deletion-invariant test, and any
sandboxed run **must not** clobber that live data. Set `IGA_STATE_DIR` to
redirect the **entire** state tree somewhere safe:

```
IGA_STATE_DIR=/tmp/sandbox python3 skills/habit-tracker/engine/producer.py
# reads  /tmp/sandbox/habits/<name>.log
# writes /tmp/sandbox/widgets/habit-tracker-habit-grid.json
```

Precedence: `$IGA_STATE_DIR` (explicit state root) > `$IGA_HOME`/state
(repo-root override) > `~/Gaia/state` (default — live data, unchanged).
When `$IGA_STATE_DIR` is set, **nothing** under the real `~/Gaia/state`
is read or written. Every producer/habit test and the Swift
deletion-invariant test sets this to a temp dir; a guard test
(`test_isolation_guard_real_state_untouched_by_producer`) asserts the
real widget JSON is byte- and mtime-unchanged across producer runs.

## Next generalization (documented, not built here)

The coach line is intentionally **deterministic and LLM-free** so this proof
stays reproducible and testable. The documented next step is an LLM-written
coach line via an iga-proactive `nudge` job: add a `proactive:` block here with
a `manual`/`schedule` trigger whose worker reads the same log, writes a richer
`coach.text`, and the producer/host keep rendering the same data-file contract
unchanged. The widget contract does not change — only the `coach.text`
producer does.

## The substrate (Iga v3 Wave A) — a superset data layer

The append-only log above is the v2 toy. Wave A adds the real data layer: a
**substrate** — a versioned, `$IGA_STATE_DIR`-rooted, atomically-written local
JSON store this skill owns. It is the first instance of the generic
**SUBSTRATE contract** (the abstraction; full spec:
`skills/iga-proactive/docs/substrate.md`, declared via the `substrate:`
frontmatter block above, analogous to `proactive:`/`widgets:`).

`engine/substrate.py` is **both** the generic, domain-agnostic contract
(`Entity`/`Event`/`GoalInterval`/`Category`/`Mapping`/`Reminder` +
`SubstrateStore`) **and** its first instance (`substrate_kind:
habit-tracker`). Nothing in the store layer hard-codes "habit" — a future
mood/sleep substrate is a different `substrate_kind` of the same contract. It
**reuses `producer.state_root()` verbatim**, so the `$IGA_STATE_DIR`
privacy/data-loss isolation is shared, not re-implemented. Stored at
`state/substrates/habit-tracker.json` (gitignored).

It is a **superset**: habits, completions (with
`amountOfCompletions`), time-bounded goal intervals (day/week/month,
`allowExceedingGoal`), categories + mappings, reminders, `archived`,
`isInverse`, color, icon/emoji, and per-completion `timezoneOffsetInMinutes`.

### Importer / exporter (anti-lock-in)

```
uv run python skills/habit-tracker/engine/import_habitkit.py \
    --input <habitkit_export.json> --state-dir <DIR>     # --state-dir REQUIRED
uv run python skills/habit-tracker/engine/export_habitkit.py \
    --state-dir <DIR> [--output out.json]
```

- **Importer** maps every HabitKit entity/field. Idempotent — keyed by
  HabitKit UUIDs, so re-import updates in place and never duplicates.
  Timezone semantics preserved exactly (HabitKit's UTC-stored local-midnight
  instant + `timezoneOffsetInMinutes` → the local civil day the user meant).
  `--state-dir` is **mandatory**: no implicit real-state default in the CLI,
  so a careless run can never write the user's live `~/Gaia/state`.
- **Exporter** rebuilds HabitKit-compatible JSON. Round-trip property:
  `import(export(S))` data-equals `S` for the supported field set, and
  `import∘export` is an idempotent fixpoint — the user is never locked in.

### Streak / goal engine

`engine/stats.py` — pure, deterministic, no I/O, no clock (you pass `today`).
Per habit: current streak, longest streak, and goal progress for the interval
active today (day/week/month, `per_day_target`, `allow_exceed`). Handles
multi-completion days (amount summed), goal changes over history
(`[start, end)` interval selection per day), `isInverse` (success = NOT doing
it), timezone (resolved to civil dates at import time), and excludes archived
habits from the active aggregate.

### Derived widget projection (the v2 app keeps working)

`engine/widget_projection.py` renders the **unchanged** v2
`contribution-grid` widget JSON as a *derived projection* of the substrate
(delegates to `producer.build_widget_data` — one widget-schema authority). The
running menu-bar app keeps rendering whether the data came from the legacy log
or the substrate. The OSS/example path stays synthetic.

```
uv run python skills/habit-tracker/engine/widget_projection.py [--days N]
```

### Round-trippability status (honest)

Every HabitKit field in the export shape is either a first-class substrate
field or preserved in a per-record `attrs` bag for lossless round-trip. The
full export (habits, completions, intervals, categories, categoryMappings,
reminders — every documented field) round-trips with the fixpoint test
passing. No HabitKit field in the documented export schema is currently
*non*-round-trippable. If HabitKit adds a field, the forward-compatible parser
preserves unknown fields on load but the exporter only re-emits known ones —
that gap (if it ever arises) is flagged here, not hidden.

## Wave D — interaction model & coaching policy (app contract)

The app stays render+relay only; these are engine/projection policies it
renders, not logic it owns.

**Per-day rendering.** A habit with a per-day target > 1 (the source app
`requiredNumberOfCompletionsPerDay`, surfaced as `goal.per_day_target`, with
each cell carrying its raw `amount`) renders each day square as a
**continuous percentage ring** (`amount / per_day_target`) — never N discrete
segments, so a 10-rep and a 500-rep goal look identically clean. The square
is **solid** once the day meets target, an **empty ring outline** at zero
(still actionable), partial in between. Binary/period/no-target habits keep
the flat fill.

**Logging.** A binary habit toggles done/undone on tap (one tap = done). A
per-day-goal habit opens a quick-log **drawer** (− / current·target
/ ＋, batch chips 1·5·10·50·100, Reset→0, Fill Day→target). Every drawer
control names an absolute amount and relays it through the *single sanctioned
record seam* (`record.py --set-amount`); the engine clamps/derives
streak/goal/level and re-emits. The drawer shows engine truth, never a local
guess. There is no blunt one-tap-complete of a 40-rep day.

**Management.** `engine/manage.py` is the sole sanctioned seam for
rename / delete (cascading) / set-goal / reorder / **archive
(graduate)** / **set-color** / import / export, mirroring `record.py`'s
mandatory-`--state-dir`, isolation, and re-emit contract. Archive flips
`Entity.archived` (history kept; the projection already excludes archived,
so it drops from the active widget + focus count) — the actionable other
half of the focus advisory. Set-color writes `Entity.attrs['color']` (a
`#rgb`/`#rrggbb` the projection passes through verbatim); the app's
ColorPicker sends the hex, `HabitManageSheet.hex` being the exact inverse
of the projection's hex→Color.

**Coaching is salient-only.** `widget_projection._salient_coach` emits a
short (≤ `COACH_MAX_CHARS`) line **only** at a behaviour-change decision
point — streak at risk, just slipped, an earned milestone / personal best,
or dormant ≥7 days. A cruising habit (and a never-started one) is
**silent**: the flame + filled square are the reward, and a per-habit wall
of "keep going!" is habituating noise. Deterministic, stdlib, no LLM, over
the same success set the streak uses (the nudge never disagrees with the
numbers). The widget `coach` field is `""` when silent; the UI renders no
line and never truncates (the cap guarantees it fits two short lines).
Each non-silent line also carries `coach_kind` (semantic, drives the icon
without prose-parsing) and `coach_tip` (the longer James Clear / *Atomic
Habits* principle shown in the hover popover; ≤ `TIP_MAX_CHARS`). Invariant:
empty line ⇔ empty kind ⇔ empty tip.

**"Too many habits" focus advisory (Atomic Habits).** The top-level `focus`
block (`widget_projection._focus_advice`) is a calm, deterministic advisory
the app renders ONCE below the last habit — and ONLY when the active set
exceeds the focus budget. Rationale: willpower is finite (Clear/Fogg —
build a small set deliberately); a habit that is already automatic (Lally
et al.: sustained high adherence) no longer needs a focus slot, so it
proposes *graduating* (archiving) the automatic ones to free attention.
`show` is false (UI renders nothing) within budget. Defaults are
Atomic-Habits-grounded and env-overridable (generic, no user data):

| Knob | Env | Default | Meaning |
|---|---|---|---|
| focus budget | `IGA_HABIT_FOCUS_BUDGET` | 4 | active habits before advising |
| graduate % | `IGA_HABIT_GRADUATE_PCT` | 80 | consistency ⇒ "automatic" |
| recency window | `IGA_HABIT_FOCUS_WINDOW_DAYS` | 30 | days the % is measured over |

Consistency = done-days / window (full-window denominator, so a young
habit can't false-positive). `candidates` lists the automatic habits
(≥ graduate %, sorted desc); when none qualify the message instead advises
pausing the weakest. Render-only — the app computes none of this.

## Iga integration — the assistant actually USES this (Wave D)

The widget is the *human* surface; these are how **Iga** consumes the same
state so she can coach + hold the user accountable (the whole point — not a
self-contained tracker she's blind to):

**Read path (assistant context).** `engine/summary.py` is the sanctioned,
read-only, deterministic, `$IGA_STATE_DIR`-isolated digest. It reuses the
FROZEN `widget_projection.build_habits_widget_from_substrate` (no new math,
no LLM, no clock except explicit `--today`) and prints compact Markdown
(default — for `/gm` + chat context) or `--json` (for tools): date,
done-today, the focus advisory, the salient nudges by kind
(at-risk/slipped/dormant) + coach line, milestones, archived count. Iga
reads this (directly or shelled from `/gm`) and reasons FROM it — never
guesses habit state. Mutates nothing.

```
IGA_STATE_DIR=~/Gaia/state uv run python \
  skills/habit-tracker/engine/summary.py [--today YYYY-MM-DD] [--json]
```

`/gm` wiring lives in the user's `rules/commands.local.md` (step-7
override) — it supersedes the legacy MemPalace `user/habits` notion with
this engine-backed digest + an accountability prompt.

**Notifications.** The menu-bar app fires ONE coalesced
habit-accountability notification per civil day (via the same `Notifier`
the proactive engine uses) listing the habits whose engine-decided
`coach_kind` ∈ {at-risk, slipped, dormant} — "Iga · habits — 3 need you
today: …". Deduped per day via UserDefaults (survives relaunch), primed so
a cold launch never backlogs, capped so the banner stays glanceable.
Milestone is intentionally NOT pushed (positive reinforcement is its own
reward). Pure presentation of an engine-decided value; the app infers
nothing.

## Proactive / Killswitch (BINDING — the proactive job is OFF by default)

The ALWAYS-ON Iga path is deterministic and needs no engine: `/gm` runs
`engine/summary.py`; the menu-bar app fires the daily accountability
notification. Neither calls an LLM.

`proactive.yaml` adds an OPT-IN deeper layer. The generic engine
**discovers** it (parses, validates, shows in a scan) but **spawns nothing
unattended**:

- The trigger is a MemPalace room poll (`habit-reflection-queue`), not a
  clock/file poll. **The room is empty by default → zero candidates → zero
  workers.** The empty room *is* the killswitch — the exact safety property
  `newsletter-research` / `iga-proactive-research` rely on.
- Engine-wide belt-and-braces: `IGA_PROACTIVE_SPAWN=0` also suppresses
  every spawn globally.

**Arm it** (when you want a deeper weekly reflection): file a MemPalace
drawer in room `habit-reflection-queue` (metadata `title`, `target_date`,
optional `triggered: false`). The next `/gm`/`/back` scan queues exactly
one gated worker (`engine/reflection.prompt.md`); the ledger cooldown
guarantees no duplicate within 72h. It files ONE short personalized
reflection drawer to `user/habits-reflection` and surfaces it in the next
brief. The `proactive:` SKILL frontmatter is a scalar pointer (yields zero
jobs / zero schema errors — no false board noise).

## Running the tests

```
cd <repo-root> && uv run python -m pytest skills/habit-tracker/tests/ -q
```

Covers v2 grid math (streak, level bucketing, date window), schema
correctness, the empty-log graceful path, **and** Wave A: the generic
substrate contract (serialization stability, forward-compat, isolation),
importer (every field, idempotent re-import, tz edge), the **round-trip
fixpoint** (`import(export(S)) == S`), the streak/goal engine (table-driven:
inverse, multi-completion days, mid-history goal changes, week/month goals,
`allow_exceed`, archived exclusion, tz-edge), the derived widget projection,
and a combined data-loss + privacy guard (no real export referenced; real
`~/Gaia/state` byte/mtime-unchanged across the full pipeline).

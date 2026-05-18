---
name: iga-proactive
description: Generic proactive engine — exact-match sqlite idempotency ledger + single global budget governor that make duplicate background-worker spawning and quota blowouts structurally impossible. Any skill declares background work via a proactive: block; the engine discovers, dedups, budget-gates, and emits worker requests. Ships a read-only macOS menu-bar companion.
intent_triggers:
  - proactive engine
  - proactive job
  - background worker
  - idempotency ledger
  - budget governor
  - scan tick
  - iga-proactive
prerequisites:
  - name: uv-on-path
    description: Engine is run via `uv run python -m engine`; uv must be on PATH (repo house style).
    check: cmd(uv)
    severity: warning
  - name: python-stdlib-sqlite
    description: Engine core uses stdlib sqlite3 only; no third-party runtime deps.
    check: cmd(python3)
    severity: error
  - name: state-dir-writable
    description: Ledger/governor db lives at $IGA_PROACTIVE_DB or ~/Gaia/state/proactive.db; parent dir must be creatable.
    check: any(env(IGA_PROACTIVE_DB), file(~/Gaia))
    severity: warning
  - name: todoist-token
    description: The todoist() trigger reads ~/.config/todoist/token or $TODOIST_API_TOKEN. Absent → todoist jobs yield nothing (graceful, not an error). Only needed if a proactive job uses a todoist() trigger.
    check: any(env(TODOIST_API_TOKEN), file(~/.config/todoist/token))
    severity: info
  - name: mempalace-server
    description: The mempalace() trigger and research-output surfacing query MemPalace. Not required for engine correctness, ledger, or governor.
    check: mcp(IgaMemory)
    severity: info
  - name: swift-toolchain
    description: Building the optional macOS menu-bar companion app requires the Swift toolchain (Xcode / Swift 5.9+). The engine itself never needs Swift; deleting the app leaves /gm working.
    check: cmd(swift)
    severity: info
triggers:
  - kind: cli
    spec: "`uv run python -m engine scan [--dry-run] [--json] [--db PATH] [--state PATH]` — one scan tick. Honours IGA_PROACTIVE_* env. No daemon; the inline path is /gm shelling out, the optional menu-bar app schedules the same one command."
mempalace_wings:
  - iga/tooling/iga-proactive
mcp_dependencies: []
status: stable
---

# Iga — Proactive Engine

A generic engine that lets any skill declare background work via a `proactive:`
block in its SKILL.md frontmatter (or a sibling `proactive.yaml`), and runs that
work **safely**: exactly once per idempotency key, under a single global budget
ceiling. The engine *decides*; entrypoints (the `/gm` inline shell-out, the
optional menu-bar app) only *relay*.

The full pipeline: **discover → trigger eval → exact sqlite ledger dedup →
single global governor → dispatcher (WORKER_REQUEST + JSON state file) →
surfacer → optional read-only menu-bar app**. See `README.md` for the
architecture and `docs/` for the authoring, architecture, security, and
OSS-publishing references.

## Why this engine exists (the failure that IS the spec)

A prior bespoke research-only proactive system **spawned 4 duplicate
background workers for one topic and burned ~70% of a 5-hour quota window**.
Root cause: it used semantic vector search to answer "did I already do
this?" — which cannot do exact idempotency, and it had no global budget
accountant.

Wave 1 builds the two runtime services that make that failure *structurally*
impossible:

- **`engine/ledger.py`** — an exact-match sqlite idempotency + cooldown
  ledger whose `claim()` is atomic (`BEGIN IMMEDIATE` + PK upsert). Under N
  concurrent claims for one key, exactly one wins. No fuzzy matching.
- **`engine/governor.py`** — a single global budget governor above ALL jobs:
  rolling 5h / 24h invocation windows + a 5h est-token ceiling, with a
  windowed circuit breaker that stays tripped until the window rolls.

Plus `engine/schema.py` — the parser/validator for the `proactive:` job block.

## Status

`status: stable` — the engine and its entrypoints are complete and frozen.

**Shipped:** `schema` (parser/validator), `ledger` (atomic idempotency +
cooldown), `governor` (single global budget + windowed circuit breaker),
`triggers` (schedule/todoist/mempalace/manual evaluators; calendar/watch are
declared Wave 3 stubs that raise `NotImplementedError`), `runtime.scan_tick`
(pure orchestration), `dispatcher` (WORKER_REQUEST + v1 JSON state file),
`surfacer` (`/gm` + `/back` payload), `cli` (`python -m engine scan`), the
declarative research job (`skills/iga-proactive-research/proactive.yaml`), and
the optional read-only macOS menu-bar companion (`app/`). Full unit suites for
both the engine (Python) and the app (Swift), including the mandatory
4-concurrent-claim regression.

**Deliberately deferred:** `calendar()` / `watch()` trigger evaluators (Wave 3
stubs with a fixed interface); a long-running daemon (the inline shell-out +
menu-bar scheduler cover the cadence need). OSS relocation into
`community_skills/` is a documented publish-time step — see
`docs/oss-publishing.md`.

## The `proactive:` job-block schema

A skill opts in by adding a `proactive:` list to its SKILL.md frontmatter.
Each entry:

| field | req | meaning |
|---|---|---|
| `id` | yes | unique job id within the skill |
| `trigger` | yes | raw expr; one of `todoist(...)`, `schedule(cron)`, `mempalace(...)`, `calendar(window:48h)`, `watch(predicate)`, `manual`. Stored raw + parsed `kind`/args. `schedule`/`todoist`/`mempalace`/`manual` are evaluated; `calendar`/`watch` are Wave 3 stubs (parse fine, raise `NotImplementedError` at eval — the runtime skips that one job, never aborts the tick). |
| `condition` | no | raw predicate string; evaluated by a tiny `eval`-free predicate language in `runtime.eval_condition` (fail-open — the ledger + governor are the real guards) |
| `action` | yes | raw expr e.g. `spawn_worker(prompt: x.md, depth: deep)`; stored raw + parsed name/args |
| `idempotency_key` | yes | template string, may contain `{{...}}` placeholders — kept verbatim by the schema; rendered per-candidate by `runtime.render_template` (missing key → empty string) |
| `budget` | no | mapping, e.g. `{ model: claude-opus-4-7[1m], wall_min: 30 }` (or `est_tokens:`). Drives the governor's token accounting |
| `deliver` | no | one of `surface_next_brief` (default), `slack_dm`, `todoist_comment`, `push`, `interrupt` |
| `cooldown` | yes | duration string (`48h`, `7d`, `1h30m`); parsed to seconds, must be positive |

Example:

```yaml
proactive:
  - id: prep-research
    trigger: todoist(label:iga-research, due:<7d)
    condition: not exists drawer for task
    action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)
    idempotency_key: research::{{task.id}}::{{task.due}}
    budget:
      model: claude-opus-4-7[1m]
      wall_min: 30
    deliver: surface_next_brief
    cooldown: 48h
```

Parse with `engine.schema.parse_jobs(skill_md_text_or_dict) -> list[Job]`.
The full field-by-field reference, every trigger kind, and a copy-pasteable
worked example live in **`docs/authoring-jobs.md`** — that is the doc a
third-party skill author should read.

## Engine usage contract (for later waves)

Every dispatch MUST be gated:

```python
if ledger.should_skip(key):           # exact, not semantic
    return
if not ledger.claim(key, job_id, cooldown_s):  # atomic; one winner
    return
d = governor.allow(model, est_tokens)          # single global accountant
if not d.ok:
    ledger.mark(key, "failed"); return         # do NOT spawn
spawn_worker(...)                              # later wave
governor.record(model, est_tokens)             # AFTER successful spawn only
ledger.mark(key, "done", output_ref=...)
```

The db path is `$IGA_PROACTIVE_DB` or `~/Gaia/state/proactive.db`. It is
gitignored (`*.db`).

## Hard boundary

**The engine decides. The daemon/app only render + relay.**

- All idempotency, budget, and skip logic lives in `engine/`. No scheduler,
  menu-bar app, or hook is allowed to re-implement or bypass it.
- Deleting any future entrypoint (launchd agent, menu-bar app, daemon) MUST
  leave the inline path working: `/gm` calling the engine in-session
  continues to function with zero external infrastructure.
- Later-wave entrypoints are thin: detect → call engine → render result.
  They never make admission decisions themselves.

## Running the tests

Engine (Python):

```
cd <repo-root> && uv run python -m pytest skills/iga-proactive/tests/ -q
```

The suite includes the mandatory 4-concurrent-claim regression
(`test_ledger.py::test_four_concurrent_claims_exactly_one_winner`).

App (Swift) — **always** use `--enable-xctest`. This is an XCTest-only
package; on toolchains where Swift Testing is the default discovery path,
plain `swift test` can silently run **0 XCTest cases and still exit 0** (false
green). `--enable-xctest` makes discovery explicit:

```
cd skills/iga-proactive/app && swift test --enable-xctest
```

## More documentation

| Doc | What it covers |
|---|---|
| `README.md` | Architecture, quickstart, env vars / killswitches, the test footgun |
| `docs/authoring-jobs.md` | How a third-party skill adds a proactive job (full schema reference) |
| `docs/architecture.md` | Engine↔entrypoint contract: ledger schema, v1 state schema, WORKER_REQUEST, app invariant |
| `docs/security.md` | Tokens, MemPalace access, worker tool scope, unsigned-app posture |
| `docs/oss-publishing.md` | The three-layer OSS model and the deferred relocation map |
| `app/README.md` | Build / install / launch / uninstall / OS-permission checklist |
| `CHANGELOG.md` | Build history |

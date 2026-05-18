# iga-proactive — generic proactive engine

<!--
  OSS UPSTREAM TEMPLATE — community_skills/iga-proactive/

  This is the generic, PII-free upstream copy. The live install target is
  skills/iga-proactive/ (a non-breaking sibling; this mirror does NOT relocate
  or repath it). `/gaia install iga-proactive` copies this directory tree to
  skills/iga-proactive/ and stamps provenance frontmatter (source,
  source_commit, installed_at) onto the INSTALLED SKILL.md — community_*/
  upstream copies deliberately carry NO provenance (it is stamped at install
  time, matching the community_rules/ convention). User personalization goes in
  the gitignored skills/iga-proactive/SKILL.local.md and a sibling skill's own
  proactive.yaml — never in this upstream tree. See docs/oss-publishing.md.

  Differences vs the live skill (sanitization only — zero logic change):
    - engine/__main__.py, engine/cli.py: help-text example path genericized
      to use ~/Gaia/... instead of an absolute user home path (cosmetic; not executed)
    - app/Tests/.../Fixtures/state_queued.json, state_empty.json: hardcoded
      personal absolute paths + personal skill names replaced with generic
      ~/Gaia/skills/example-* placeholders (test assertions do not depend on
      these string VALUES — only on counts / nullness, which are unchanged)
    - the personal research job (iga-proactive-research/proactive.yaml) is a
      SEPARATE personal skill and is intentionally NOT copied here; a sanitized
      copy-paste template ships as proactive.example.yaml instead
    - build artifacts (app/.build/, app/Iga.app/) excluded

  Standalone test status: the Python engine package is path-independent —
  `cd community_skills/iga-proactive && uv run python -m pytest tests/ -q`
  passes 120/120 from this directory with no repo-root assumption. The Swift
  app suite must still be run with `swift test --enable-xctest` (see the
  test-footgun note below).
-->

A skill-agnostic engine that lets **any** skill declare background work in a
`proactive:` block and runs it **safely**: exactly once per idempotency key,
under a single global budget ceiling. It discovers jobs, evaluates triggers,
dedups atomically, budget-gates globally, emits structured worker requests, and
surfaces completed work at the next natural touchpoint. An optional read-only
macOS menu-bar app fronts it.

> The engine *decides*. Entrypoints (the `/gm` inline shell-out, the menu-bar
> app) only *relay*. Deleting every entrypoint leaves the inline path working
> with zero external infrastructure.

## The failures are the spec

A prior bespoke research-only proactive system **spawned 4 duplicate
background workers for one topic and burned ~70% of a 5-hour quota window**.
Two root causes:

1. It used **semantic vector search** to answer "did I already do this?" —
   which fundamentally cannot do exact idempotency.
2. It had **no global budget accountant** — per-topic logic spawned workers
   independently with no ceiling above all of them.

This engine makes both failures *structurally* impossible, not merely
unlikely:

- **`engine/ledger.py`** — an exact-match sqlite idempotency + cooldown
  ledger. `claim()` runs the whole read-decide-write inside one
  `BEGIN IMMEDIATE` transaction with a `PRIMARY KEY` on `idempotency_key`.
  Under N concurrent claims for one key, **exactly one wins**. No fuzzy
  matching. (Regression: `tests/test_ledger.py::test_four_concurrent_claims_exactly_one_winner`.)
- **`engine/governor.py`** — a single global budget governor above ALL jobs:
  rolling 5h / 24h invocation windows + a 5h est-token ceiling, with a
  *windowed* circuit breaker that stays tripped until the offending window
  rolls (no timer, no manual reset — the window IS the reset).

## Architecture

```
skills/*/SKILL.md (proactive:)        ┐
skills/*/proactive.yaml               ┘  discover_job_sources
        │
        ▼
   schema.parse_jobs           parse + validate every job (stdlib, no pyyaml)
        │
        ▼
   triggers.evaluate           schedule / todoist / mempalace / manual
        │                      → fired Candidates (calendar/watch = Wave3 stub)
        ▼
   runtime.eval_condition      tiny eval-free predicate (fail-open)
        │
        ▼
   runtime.render_template     {{task.id}} → concrete idempotency_key
        │
        ▼
   ledger.should_skip + claim  EXACT atomic dedup — the anti-duplicate point
        │
        ▼
   governor.allow              single global ceiling — deny → ledger.mark(failed)
        │
        ▼
   dispatcher.build_dispatch   QueuedCandidate → WORKER_REQUEST[] + v1 JSON state
        │
        ▼
   <entrypoint dispatches>     /gm reads WORKER_REQUESTs, runs workers (Agent)
        │
        ▼
   surfacer.build_surface      done drawers → 📑 lines for /gm + /back
        │
        ▼
   app/  (optional)            read-only macOS menu-bar: render + relay + trigger
```

Why this shape kills the two failures by construction:

- **Duplicate spawn is impossible** because the *only* place a candidate
  becomes eligible is `ledger.claim()`, which is atomic and exactly-one-winner.
  Two ticks (or two threads) racing the same rendered key — exactly one
  proceeds; the loser is dropped. The cap-trim at the end keeps trimmed
  candidates' `claimed` rows, so they don't re-spawn within cooldown either.
- **Quota blowout is impossible** because every candidate that wins its claim
  must still pass `governor.allow()` — one accountant, one ceiling, above all
  jobs. A denied candidate is `mark`ed `failed` (cooldown still holds, so no
  retry-storm) and never spawned.

The engine **never** spawns a subagent itself. It emits `WORKER_REQUEST`
dicts; the calling Claude Code session (e.g. `/gm`) dispatches the actual
workers via its own Agent tool. See `docs/architecture.md`.

## Quickstart — run a scan

From the repo root (the repo uses `uv`):

```sh
cd <repo-root>/skills/iga-proactive
PYTHONPATH=engine uv run python -m engine scan --dry-run
```

`--dry-run` runs real detection + condition + key-rendering against a
**throwaway temp db** and prints what *would* be queued — it mutates nothing
(no ledger row, no state file). Drop `--dry-run` for a real tick (writes the
ledger + the JSON state file). Add `--json` for one machine-readable object
(tick stats + queue + state path). Exit code is 0 on every normal path,
including zero candidates and a missing Todoist token.

The one command the menu-bar app is allowed to exec is exactly:

```sh
cd ~/Gaia/skills/iga-proactive && PYTHONPATH=engine uv run python -m engine scan --json
```

## Env vars & killswitches

| Var | Effect |
|---|---|
| `IGA_PROACTIVE_RESEARCH=0` | **Killswitch.** Engine emits an explicit empty result, writes no state, mutates no ledger. Exit 0 (a disabled engine is not an error). Accepts `0/false/off/no`. |
| `IGA_PROACTIVE_SPAWN=0` | Detect + dedup-preview but **do not** mutate the ledger and **do not** write state — identical to `--dry-run`. Accepts `0/false/off/no`. |
| `IGA_PROACTIVE_DB` | Ledger + governor sqlite db path. Default `~/Gaia/state/proactive.db` (parent auto-created; gitignored via `*.db`). |
| `IGA_PROACTIVE_STATE` | v1 JSON state-file path. Default `~/Gaia/scratch/proactive-state.json` (`scratch/` is gitignored, keeps `git status` clean by construction). |
| `IGA_MAX_SPAWN_PER_TICK` | Operational cap on queued candidates per tick (overrides the SKILL.md `engine_config:` value). Trimmed candidates keep their `claimed` row, so they defer — they don't lose the dedup guarantee. |
| `TODOIST_API_TOKEN` / `~/.config/todoist/token` | Todoist auth for `todoist()` triggers. Absent → those triggers yield nothing (graceful, exit 0). |

## The test footgun (read this)

**Engine (Python):**

```sh
cd <repo-root> && uv run python -m pytest skills/iga-proactive/tests/ -q
```

**App (Swift) — always pass `--enable-xctest`:**

```sh
cd skills/iga-proactive/app && swift test --enable-xctest
```

This is an XCTest-only package. On the toolchains verified here (Swift 6.2.x)
plain `swift test` does run the 14 XCTest cases — but on
toolchains/configurations where Swift Testing is the default discovery path,
plain `swift test` can **silently execute 0 XCTest cases and still exit 0** (a
false green: "Test run with 0 tests … passed"). `--enable-xctest` makes XCTest
discovery explicit; rely only on that form in CI or before publishing. The
Swift suite includes `ContractLitmusTests` which source-greps every
`Sources/*.swift` file and fails the build if any forbidden write / subprocess
primitive escapes the single sanctioned `ContractGuard` seam.

## Deeper docs

| Doc | Audience |
|---|---|
| `docs/authoring-jobs.md` | Skill authors adding a `proactive:` block (full schema reference + worked example) |
| `docs/architecture.md` | Maintainers: ledger schema, v1 state schema, WORKER_REQUEST, engine↔app contract |
| `docs/security.md` | Adopters: tokens, MemPalace access, worker tool scope, unsigned-app posture |
| `docs/oss-publishing.md` | The three-layer OSS model + the deferred relocation map |
| `app/README.md` | The macOS menu-bar companion: build / install / launch / uninstall |
| `CHANGELOG.md` | Build history |
| `SKILL.md` | The LLM-facing manifest (frontmatter + usage contract) |

# Changelog — iga-proactive

All notable changes to the generic proactive engine and its menu-bar
companion. Dates are the work date; all entries land on branch
`feat/proactive-engine-v1`.

## 2026-05-16 — `feat/proactive-engine-v1`

Engine + entrypoints complete and frozen. `status: building → stable`.

### Wave 1 — correctness core
- `engine/schema.py` — stdlib-only parser/validator for the `proactive:`
  job block (no pyyaml); `Job`/`Trigger`/`Action` dataclasses; duration
  parsing; `{{...}}` keys kept verbatim.
- `engine/ledger.py` — exact-match sqlite idempotency + cooldown ledger;
  atomic `claim()` (`BEGIN IMMEDIATE` + PK upsert), exactly-one-winner.
- `engine/governor.py` — single global budget governor; rolling 5h/24h
  invocation windows + 5h est-token ceiling; windowed circuit breaker.
- Full unit suites incl. the mandatory 4-concurrent-claim regression.

### Wave 2 — orchestration + entrypoints
- `engine/triggers.py` — schedule / todoist / mempalace / manual
  evaluators (all I/O injectable, fail-graceful); calendar / watch are
  Wave 3 stubs that parse but raise `NotImplementedError` at eval.
- `engine/runtime.py` — `scan_tick`: pure orchestration wiring discovery →
  trigger → condition → key render → ledger → governor → queue; per-tick
  spawn cap; one bad skill never aborts the tick.
- `engine/dispatcher.py` — `QueuedCandidate` → WORKER_REQUEST[] + the v1
  JSON state file (atomic write, schema_version 1).
- `engine/surfacer.py` — completed-research → capped `/gm`+`/back` 📑
  payload; refreshes the same state file; no MCP imports (injected
  resolver entry point).
- `skills/iga-proactive-research/proactive.yaml` — the bespoke research
  scanner ported to a declarative two-job `proactive:` block.

### CLI
- `engine/cli.py` + `engine/__main__.py` — `python -m engine scan`
  (`--dry-run`, `--json`, `--db`, `--state`); thin relay, zero admission
  decisions; honours `IGA_PROACTIVE_RESEARCH=0` / `IGA_PROACTIVE_SPAWN=0`.

### macOS menu-bar companion (`app/`)
- SwiftUI `MenuBarExtra` app (macOS 13+, `LSUIElement`): render + relay +
  trigger only, zero job logic in Swift. `ContractGuard` is the single
  sanctioned engine-exec entry point; `LedgerReader` is driver-level read-only.
- `ContractLitmusTests` — source-grep + runtime assertions enforcing the
  hard contract and the deletion invariant.
- Scheduler host (`NSBackgroundActivityScheduler` + wake trigger) as the
  launchd replacement; notifications; login-item management.

### Discovery fix
- Distinguish "not a proactive skill" (no frontmatter or no `proactive:`
  key → silent skip, counted in `skipped_non_proactive`, never an error)
  from a genuinely malformed `proactive:` block (real `errors[]` entry,
  that one skill skipped). Removes false red noise in the menu-bar app.

### Install + UI polish
- `build.sh` installs to `~/Applications/Iga.app` (per-user, no sudo) and
  reindexes Spotlight; repo-local `./Iga.app` kept for development.
  Unsigned / un-notarized by frozen decision.
- Menu-bar UI polished: health pill, big counts row, governor meters
  colored by headroom, contained count-summarized error disclosure,
  relative timestamps with absolute on hover, light/dark support.

### Documentation (this pass)
- `SKILL.md` frontmatter completed (intent_triggers, full prerequisites
  incl. uv / Todoist / MemPalace / Swift, CLI trigger); `status: stable`.
- Rewrote `README.md` (architecture + "the failures are the spec" +
  quickstart + env/killswitches + the `--enable-xctest` note).
- Added `docs/authoring-jobs.md`, `docs/architecture.md`,
  `docs/security.md`, `docs/oss-publishing.md`.
- Refreshed `app/README.md` to the final UI and the install/permission
  checklist. Added this `CHANGELOG.md`.

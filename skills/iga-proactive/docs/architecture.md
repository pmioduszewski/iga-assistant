# Architecture — the engine↔entrypoint contract

This is the maintainer doc: the exact data contracts between the engine and
whatever runs on top of it. Everything here is verified against the source in
`engine/` and `app/Sources/IgaMenuBar/`.

## The hard boundary

**The engine decides. Entrypoints only render + relay + trigger.** All
idempotency, budget, cooldown, and skip logic lives in `engine/`. No
entrypoint (the `/gm` inline shell-out, the menu-bar app, any future daemon)
re-implements or bypasses it. Deleting every entrypoint must leave the inline
path — `scan_tick` called in-session via `/gm` — working with **zero external
infrastructure**.

## Inline vs daemon model

- **Inline (production today):** `/gm` / `/back` shell out to
  `python -m engine scan` (or call `runtime.scan_tick` in-process), read the
  `WORKER_REQUEST[]`, and dispatch the actual workers via the session's own
  Agent tool. The engine spawns nothing itself.
- **Menu-bar app (optional convenience):** schedules the *same one command*
  on an OS-coalesced cadence + on wake, and renders the state file / ledger
  read-only. It is a front-end, never a dependency.
- **Long-running daemon:** deliberately not built. The inline shell-out plus
  the menu-bar scheduler cover the cadence need without a resident process.

## The sqlite ledger schema

One sqlite db (`$IGA_PROACTIVE_DB` or `~/Gaia/state/proactive.db`, WAL mode,
gitignored via `*.db`). Two tables, defined once in `engine/ledger.py::_SCHEMA`
and reused by the governor:

```sql
CREATE TABLE IF NOT EXISTS job_runs (
    idempotency_key TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    last_run_ts     TEXT NOT NULL,            -- ISO-8601 UTC
    status          TEXT NOT NULL CHECK(status IN
                        ('claimed','running','done','failed','timeout')),
    output_ref      TEXT,
    cooldown_until  TEXT NOT NULL             -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS dispatch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,                -- ISO-8601 UTC
    job_id      TEXT NOT NULL,
    model       TEXT NOT NULL,
    est_tokens  INTEGER NOT NULL DEFAULT 0
);
```

- **`job_runs`** is the idempotency + cooldown ledger. The `PRIMARY KEY` on
  `idempotency_key` plus `BEGIN IMMEDIATE` in `Ledger.claim()` is the
  exactly-one-winner guarantee. A row is "live" (claim refused, `should_skip`
  true) iff `status IN ('claimed','running')` **or** `cooldown_until > now`.
  `claim()` upserts over a stale (expired + terminal) row.
- **`dispatch_log`** is the governor's append-only audit. `Governor` derives
  every window count by `SUM(CASE WHEN ts > cutoff …)` over this table. A
  dispatch is recorded **only after a successful spawn** (recording a spawn
  that never happened poisons the windows and falsely trips the breaker).

Status lifecycle: `claim()` writes `claimed`; the entrypoint transitions via
`ledger.mark(key, status, output_ref=...)` to `running` → `done` (with an
`output_ref`) or `failed`/`timeout`. `runtime.scan_tick` itself only ever
`mark`s `failed` (governor denial — keeps the cooldown so a denied job does
not retry-storm).

## The v1 JSON state file

`dispatcher.build_state` writes it; `surfacer.refresh_state` overlays a subset
of the same schema. Path: `$IGA_PROACTIVE_STATE` or
`~/Gaia/scratch/proactive-state.json` (`scratch/` is gitignored — keeps
`git status` clean by construction). Written atomically (`tmp` + `os.replace`)
so a polling reader never sees a half-written file. `STATE_SCHEMA_VERSION = 1`.

Full document (`dispatcher.build_state`):

```json
{
  "schema_version": 1,
  "generated_at": "<ISO-8601 UTC, datetime.isoformat()>",
  "tick": {
    "discovered_jobs": 0,
    "fired_candidates": 0,
    "condition_skipped": 0,
    "claim_skipped": 0,
    "governor_denied": 0,
    "queue_alert": false,
    "skipped_non_proactive": 0,
    "errors": ["..."]
  },
  "queue": [ WORKER_REQUEST, ... ],
  "counts": { "queued": 0, "running": 0, "done": 0 },
  "governor": {
    "invocations_5h": 0,  "max_invocations_5h": 8,
    "invocations_24h": 0, "max_invocations_24h": 20,
    "est_tokens_5h": 0,   "max_est_tokens_5h": 2000000
  }
}
```

`surfacer.refresh_state` writes a **subset** (no `tick`, no `queue`) — it
overlays the latest surface + live ledger counts without recomputing a tick:

```json
{
  "schema_version": 1,
  "generated_at": "<ISO-8601 UTC>",
  "surface": { "lines": ["📑 acme: ..."], "shown": 1, "total": 1, "overflow": null },
  "counts": { "queued": 0, "running": 0, "done": 0 },
  "governor": { ... }
}
```

The Swift decoder (`app/Sources/IgaMenuBar/EngineState.swift`) makes **every**
field optional/defaulted so a surfacer-only refresh, a partial write, or a
stale file decodes cleanly — it never throws. `governor` may carry an
`"error"` string instead of stats if `Governor.stats()` itself failed (rare;
stats failure must never break dispatch).

## WORKER_REQUEST shape

Built by `dispatcher.to_worker_request` from a `QueuedCandidate` that has
**already** won its ledger claim and passed the governor — it is a pure data
transform, no admission logic:

```json
{
  "job_id": "research-todoist",
  "idempotency_key": "research::123::2026-05-20",
  "trigger_kind": "todoist",
  "action": "spawn_worker(prompt: engine/worker.prompt.md, depth: deep)",
  "action_name": "spawn_worker",
  "prompt_path": "/abs/resolved/skills/iga-proactive-research/engine/worker.prompt.md",
  "model": "claude-opus-4-7[1m]",
  "est_tokens": 300000,
  "deliver": "surface_next_brief",
  "context": { "task.id": "123", "task.title": "...", "...": "..." }
}
```

`prompt_path` is the absolute resolution of `prompt:` from the action args,
relative to the source skill's directory; `null` if the action carries no
prompt. The `idempotency_key` is the de-dup identity end to end — it is the
ledger PRIMARY KEY *and* the menu-bar app's notification de-dup key.

## The CLI relay

`engine/cli.py` (`python -m engine scan`) is the thinnest possible relay. It
makes **zero** admission decisions:

- `scan` → `runtime.scan_tick` (real ledger claim + governor gate against the
  production db) → `dispatcher.build_dispatch` → print WORKER_REQUESTs, write
  state. Exit 0 on every normal path (incl. zero candidates, missing token).
- `scan --dry-run` (or `IGA_PROACTIVE_SPAWN=0`) → runs the *real* detection +
  condition + key rendering against a **throwaway temp db** (so `claim()` runs
  for real without touching production), then annotates each candidate with
  the verdict it *would* get against the production ledger via a read-only
  `should_skip`. Mutates nothing: no ledger row, no state file.
- `IGA_PROACTIVE_RESEARCH=0` → emits an explicit empty result, writes no
  state, mutates no ledger, exit 0.

Flags: `--dry-run`, `--json`, `--db PATH`, `--state PATH`. The only subcommand
is `scan`.

## The menu-bar app invariant

The app (`app/`) is **render + relay + trigger only — zero job logic in
Swift**. It is enforced two ways by `ContractLitmusTests` (run with
`swift test --enable-xctest`):

1. **Runtime:** `ContractGuard.documentedCommand` must be *exactly*
   `cd ~/Gaia/skills/iga-proactive && PYTHONPATH=engine uv run python -m engine scan --json`,
   and must contain no write/mutate verb (`mark`, `claim`, `record`,
   `INSERT`, `UPDATE`, `DELETE`, `--write`).
2. **Source grep:** every `Sources/*.swift` file (comments stripped) is
   scanned; the build fails if any file contains a forbidden write/subprocess
   primitive (`SQLITE_OPEN_READWRITE`, `SQLITE_OPEN_CREATE`, `.write(to:`,
   `JSONEncoder(`, `NSTask(`, …) or constructs `Process()` outside
   `ContractGuard.swift`, or references `proactive-state.json` together with a
   write primitive.

`ContractGuard` is the *single* place an engine subprocess is constructed; it
runs only the one sanctioned scan command. `LedgerReader` opens the sqlite db
with `?mode=ro` **and** `SQLITE_OPEN_READONLY` so any accidental write fails
at the driver level. The app never writes the state file and never writes the
ledger.

**Deletion invariant** (also asserted by `testDeletionInvariantIsDocumented`,
which fails the build if the app README stops documenting it): deleting
`Iga.app` removes only the scheduler host + viewer + notifier. `/gm` calling
the engine in-session continues to work with zero external infrastructure. The
app is a convenience front-end, never a dependency. Frozen decision:
MemPalace `gaia/decisions/3542bae6`.

# Security & privacy notes

An honest accounting of what this skill reads, writes, and executes, for an
OSS adopter deciding whether and how to run it. Nothing here is hidden in
prose — these are deliberate trade-offs.

## What the engine reads

- **Todoist API token.** The `todoist()` trigger reads, in order:
  `$TODOIST_API_TOKEN`, then the file `~/.config/todoist/token`. It is sent as
  a `Bearer` token to `https://api.todoist.com/api/v1/tasks?label=<label>`
  over HTTPS (stdlib `urllib`, 15 s timeout). The token never enters the
  ledger, the state file, or a WORKER_REQUEST. **Absent token → the trigger
  yields nothing** (graceful, exit 0) — it is not required for the engine,
  ledger, or governor; only for jobs that actually use a `todoist()` trigger.
  An adopter who uses Todoist jobs must supply this token themselves; store it
  with restrictive permissions (the token file is outside the repo tree and
  should be `chmod 600`).
- **MemPalace.** The `mempalace()` trigger imports the local `mempalace`
  module and calls `tool_list_drawers(room=…, limit=50)`. The surfacer
  resolves completed-work `output_ref`s via an **injected resolver closure**
  (in production an entrypoint passes a closure that reads a drawer; the
  surfacer itself never imports MemPalace). MemPalace unavailable → `[]`
  (graceful). No network leaves the machine for this path.
- **The repo's own `skills/*/SKILL.md` and `skills/*/proactive.yaml`** —
  discovered and parsed locally.

No other external network calls are made by the engine. Trigger I/O is
fail-graceful: a missing data source is "no candidates", never an error, never
a crash.

## What the engine writes

- The sqlite ledger (`$IGA_PROACTIVE_DB` or `~/Gaia/state/proactive.db`) —
  job idempotency keys, job ids, statuses, timestamps, and a dispatch audit
  log. Idempotency keys are author-defined templates rendered from candidate
  context (e.g. a Todoist task id + due date). **If your idempotency key
  embeds sensitive identifiers, they land in this local sqlite file.** It is
  gitignored (`*.db`) and never committed, but it is plaintext on disk.
- The v1 JSON state file (`$IGA_PROACTIVE_STATE` or
  `~/Gaia/scratch/proactive-state.json`) — tick stats, the queue of
  WORKER_REQUESTs (which include rendered context such as task titles/IDs),
  governor counters, and the surface lines. Gitignored via `scratch/`.
  Plaintext on disk; treat it as you would the ledger.

Both are written atomically and live under the user's home tree by default.

## Worker tool scope

The engine itself **never spawns a subagent or makes an LLM call**. It emits
`WORKER_REQUEST` records; an entrypoint (e.g. `/gm`) dispatches the actual
worker via its own Agent tool. By design and per the worker prompt the
research workers get **read-only** information tools and may **write only to
research drawers** — they do not get arbitrary write/shell scope. Idempotency
and budget admission are decided by the engine *before* any worker is
dispatched, so a misbehaving or duplicated job cannot spawn extra workers or
exceed the global budget ceiling.

## The macOS app

- **Read-only.** It opens the sqlite ledger with `?mode=ro` **and**
  `SQLITE_OPEN_READONLY` (driver-level guard) and never writes the state file.
  Its only engine side effect is exec'ing the one documented scan command.
  Enforced by `ContractGuard` + the `ContractLitmusTests` source-grep.
- **Unsigned and un-notarized — intentional, by frozen decision.** There is no
  Apple Developer ID code signing and no notarization. Consequences an adopter
  must accept:
  - First launch is Gatekeeper-blocked. The user must **right-click
    `~/Applications/Iga.app` → Open → Open** once per bundle path. This is a
    deliberate human-in-the-loop step, not a bug.
  - Launchpad may lag for an unsigned bundle; Spotlight ("Iga") and
    `open ~/Applications/Iga.app` are always reliable.
  - macOS will prompt for **Notifications** permission and for **Login Item**
    approval — these are normal OS security gates and cannot be scripted.
  - Why no signing: it would require an Apple Developer account and a CI
    signing identity, neither of which is appropriate to bake into an
    open-source skill that a third party builds locally from source. The
    reproducible `./build.sh` produces the bundle from source with no secrets;
    signing/notarization is left to a downstream packager if they want it.
    `build.sh` is intentionally kept signing-free — do not add it.

## What an OSS adopter must supply / be aware of

- A Todoist token (only if using `todoist()` jobs) at
  `~/.config/todoist/token` or `$TODOIST_API_TOKEN`, `chmod 600`.
- A MemPalace install (only if using `mempalace()` jobs or the research
  surfacing path).
- The `uv` toolchain on PATH (the engine is run as `uv run python -m engine`).
- For the menu-bar app: the Swift toolchain (Xcode / Swift 5.9+), plus
  accepting the unsigned-app posture above. The engine works fully without the
  app.
- Awareness that the ledger and state file are local plaintext containing
  whatever identifiers your job's idempotency key and candidate context
  embed. Choose key templates accordingly.
- No secrets ship in this skill. Tokens and personal config live outside the
  repo tree (token files, env vars) or in gitignored `*.local.md` overrides —
  see `docs/oss-publishing.md`.

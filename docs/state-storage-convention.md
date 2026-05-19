# State & storage convention (PROPOSAL — audit 2026-05-19, no migration yet)

Status: **proposed**, not adopted. This is the audit deliverable for the
"scattered sqlite DBs" concern. No files moved. One real bug flagged.

## Inventory (what exists today)

| Store | Path today | Writer | Convention |
|---|---|---|---|
| Proactive ledger+governor | `~/Gaia/state/proactive.db` **and** `~/Iga/state/proactive.db` | `iga-proactive/engine/ledger.py`,`governor.py` | `$IGA_PROACTIVE_DB` else **`~/Iga/state`** (code) — docstring says `~/Gaia/state` |
| Proactive run state (json) | `~/Gaia/scratch/proactive-state.json` | dispatcher | `$IGA_PROACTIVE_STATE` else `~/Gaia/scratch` |
| Habit substrate + log + widgets | `~/Gaia/state/habits/`,`/widgets/` | habit-tracker | **`$IGA_STATE_DIR`** else `~/Gaia/state` ✅ clean |
| Mood substrate | `~/Gaia/state/mood/` | mood-tracker | **`$IGA_STATE_DIR`** else `~/Gaia/state` ✅ clean |
| Finance | `~/Gaia/finance.db` (repo root) | finance tooling | none (repo root) |
| Rize cache | `~/Gaia/rize_data.db` (repo root) | rize tooling | none (repo root) |
| MemPalace | `~/Gaia/mempalace/.mempalace/palace/*.sqlite3` | mempalace pkg | own subsystem |
| Swift build | `*/app/.build/build.db` | SwiftPM | artifact (ignore) |

## The bug (flag, do not silently carry forward)

`ledger.default_db_path()` returns **`~/Iga/state/proactive.db`** (capital-I
`Iga` — a rename-in-progress artifact) while its own docstring, the menu-bar
app's `LedgerReader`, and every other store say **`~/Gaia/state`**. Result:
**two divergent proactive ledgers exist on disk.** This is why cross-session
ledger ops earlier had to target `~/Iga/state/proactive.db` while
`state.json` sat in `~/Gaia/scratch`. The fix (deferred — migration not in
scope here) is a one-liner in `ledger.py` + a one-time merge/rm of the
stale file. Tracked as a follow-up decision.

## Proposed convention (adopt later, by decision)

1. **One root: `$IGA_STATE_DIR`**, default `~/Gaia/state/` — exactly the
   habit/mood substrate resolver (proven, isolation-aware, documented).
   Precedence `$IGA_STATE_DIR` > `$IGA_HOME/state` > `~/Gaia/state`.
2. **Every persistent store lives under it**, namespaced by domain:
   - `state/proactive.db` (ledger+governor) — kill the `~/Iga` fork
   - `state/findings.db` (NEW — the sqlite finding sink, see #1)
   - `state/finance.db`, `state/rize.db` (move from repo root, later)
   - `state/habits/`, `state/mood/`, `state/widgets/` (already correct)
   - `state/scratch/` for run-state json (fold `~/Gaia/scratch` in, later)
3. **MemPalace is an explicit exception** — separate subsystem, keeps
   `mempalace/.mempalace/palace/`. Documented, not folded in.
4. **Per-store env overrides remain** (`$IGA_PROACTIVE_DB` etc.) but all
   *default* to a path derived from the single root, never a hardcoded
   `~/Gaia` / `~/Iga` literal.

Net: one resolver, one root, one mental model; tests/sandbox isolation for
free via `$IGA_STATE_DIR`. Migration is a separate, explicit step.

## What this proposal does NOT do

No files moved, no code changed, no DB migrated. The new sqlite finding
sink (#1) is built **on rule 2** (`$IGA_STATE_DIR/findings.db`) so it is
born compliant and never adds to the sprawl.

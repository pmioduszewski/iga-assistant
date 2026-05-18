# The SUBSTRATE contract

This is the maintainer doc for a **generic, domain-agnostic abstraction**
introduced in Iga v3 Wave A and proven by its first instance, the
habit-tracker (`skills/habit-tracker/`). It is the data-layer sibling of the
`proactive:` and `widgets:` declarations: where `proactive:` declares
*background work a skill wants run* and `widgets:` declares *a render surface*,
`substrate:` declares *a durable local data store a skill owns*.

It lives in `skills/iga-proactive/docs/` because iga-proactive is the home of
Iga's cross-skill engine contracts (job schema, widget host contract); the
substrate contract is the third such cross-skill contract. The reference
implementation, however, is `skills/habit-tracker/engine/substrate.py` — a
future substrate (e.g. a mood log) instantiates the same generic types with a
different `substrate_kind`; it does **not** fork the contract.

## What a substrate is

A **substrate** is a versioned, `$IGA_STATE_DIR`-rooted, atomically-written
local JSON data store that a skill owns. It is the durable source of truth a
skill reads/writes. Widgets, stats, briefs, and any projection are *derived*
from it; the substrate itself is never a projection.

The contract defines **five generic concepts**. None of them say "habit" —
habit is merely instance #1. A future "mood", "sleep", or "spend" substrate is
*a different instance of the same contract*:

| Concept | Meaning (domain-agnostic) | Habit-tracker instance |
|---|---|---|
| **entities** | the tracked things — stable `id`, display fields, `archived`, an `inverse` flag (success = NOT doing it), free-form `attrs` | a habit |
| **events** | timestamped, amount-bearing occurrences attached to an entity — local-civil `date`, `amount` (int ≥ 0), `tz_offset_min`, optional `note` | a completion (`amountOfCompletions`) |
| **goal_intervals** | time-bounded goal definitions — half-open `[start, end)` civil dates (`end` null = active), a `period` (`day`/`week`/`month`/`none`), per-period `target`, optional `per_day_target`, `allow_exceed` | a HabitKit interval |
| **categories** + **mappings** | named groupings and ordered entity↔category links | HabitKit categories / categoryMappings |
| **reminders** | per-entity weekday/time notification specs — opaque to the engine, persisted for round-trip fidelity only | HabitKit reminders |

A substrate document on disk:

```json
{
  "substrate_version": 1,
  "substrate_kind": "habit-tracker",
  "generated_at": "<ISO8601 UTC>",
  "entities":       [ ... ],
  "events":         [ ... ],
  "goal_intervals": [ ... ],
  "categories":     [ ... ],
  "mappings":       [ ... ],
  "reminders":      [ ... ]
}
```

`substrate_kind` is the **instance discriminator**. The store layer
(`SubstrateStore`) is otherwise fully domain-agnostic — it never branches on
"habit".

## Contract guarantees (binding)

1. **stdlib only**, JSON on disk, no third-party deps, no LLM in the data
   layer.
2. **Atomic writes** — tmp file + `os.replace`, identical to
   `habit-tracker/engine/producer.py::_atomic_write_json` and the iga-proactive
   dispatcher. A polling reader never observes a partial file.
3. **Round-trip-stable** — `load(save(x))` data-equals `x`. Records are
   key-sorted and lists deterministically ordered, so on-disk bytes are
   reproducible and diff-friendly. The volatile `generated_at` write-stamp is
   **excluded** from data equality (`substrate.data_equal`).
4. **`$IGA_STATE_DIR` isolation (privacy / data-loss guard)** — the substrate
   store **reuses the producer's `state_root()` resolver verbatim**. It does
   not re-implement path resolution. Precedence is unchanged:
   `$IGA_STATE_DIR` > `$GAIA_HOME/state` > `~/Gaia/state`. When
   `$IGA_STATE_DIR` is set, nothing under the real `~/Gaia/state` is read or
   written. This is the same guard that protects the user's live widget data;
   it is shared, never duplicated.
5. **Forward-compatible parse** — an unknown future field in a record is
   ignored on load (never crashes an older reader); a missing optional field
   takes its default.

Path: `state/substrates/<kind>.json` under the (isolation-aware) state root.
It is gitignored (`state/` is ignored wholesale).

## The importer / exporter round-trip contract

An instance that mirrors an external app (habit-tracker mirrors HabitKit) MUST
provide a lossless importer + exporter with this property:

```
import(export(S))  data-equals  S          # for the supported field set
```

and `import ∘ export` MUST be an **idempotent normalizer** (a second round
trip is a no-op — a true fixpoint). This is the user's anti-lock-in
guarantee: they can always get their data back out in the source app's format.

Provenance fields needed only to reproduce the *source app's* byte layout
(e.g. HabitKit's original UTC instant) are stored in the per-record `attrs`
bag, not as first-class substrate fields, and are not part of the
domain-data equality — but they make a source-originated round trip
**byte-exact**. A natively-authored record (no provenance) round-trips with
all domain fields preserved exactly and is a fixpoint after the first pass.

## Derived projections

Anything user-facing is a pure function of the substrate:

- The habit-tracker renders the **v2 `contribution-grid` widget** (the schema
  the running menu-bar app polls, `schema_version 1`) as a derived projection
  — `engine/widget_projection.py` delegates to the already-unit-tested
  `producer.build_widget_data`, so there is exactly one widget-schema
  authority and the app keeps rendering unchanged whether the data came from
  the legacy append-only log or the substrate.
- The streak/goal engine (`engine/stats.py`) is pure and deterministic: no
  I/O, no clock reads except an explicit `today` passed in. Every result is
  reproducible and table-testable.

A projection never writes back into the substrate.

## The `substrate:` SKILL.md declaration

A skill declares the substrate(s) it owns in its `SKILL.md` frontmatter,
analogous to the `proactive:` and `widgets:` blocks. It is **discovery
metadata** — generic Iga / `/gaia status` can enumerate every skill's owned
data stores without hard-coding any skill:

```yaml
substrate:
  - kind: habit-tracker              # the instance discriminator
    version: 1                       # substrate_version it writes
    path: state/substrates/habit-tracker.json   # under $IGA_STATE_DIR root
    entities: habit                  # human label for the entity concept
    importer: engine/import_habitkit.py         # optional: external-app import
    exporter: engine/export_habitkit.py         # optional: round-trippable
    derived:                                    # projections it feeds
      - widget:habit-grid
```

Field reference:

| field | req | meaning |
|---|---|---|
| `kind` | yes | the `substrate_kind` discriminator; unique per skill |
| `version` | yes | the `substrate_version` integer the skill reads/writes |
| `path` | yes | data-file path *relative to the `$IGA_STATE_DIR` root* (never an absolute real-state path in the manifest) |
| `entities` | no | human label for the entity concept (doc only) |
| `importer` | no | skill-relative path to an external-app importer CLI |
| `exporter` | no | skill-relative path to an exporter CLI; presence asserts the round-trip guarantee holds |
| `derived` | no | list of projection ids this substrate feeds (e.g. `widget:<id>`) — cross-links the `widgets:` block |

Like `proactive:`/`widgets:`, the block is **declarative discovery only** —
no scanner is required for the substrate to function; the store works
standalone. The declaration lets future generic tooling (`/gaia status`, a
data-export command) enumerate and act on every skill's owned stores
uniformly.

## OSS / privacy posture

- The generic contract + reference implementation ship in
  `skills/habit-tracker/`. When habit-tracker is OSS-published the engine
  moves to `community_skills/habit-tracker/` per the three-layer model in
  `oss-publishing.md`; the substrate **data files never ship** (`state/` is
  gitignored and lives only in the user's tree).
- Any OSS mirror ships **synthetic seed data only** — never personal content.
- An importer CLI for an external app MUST require an explicit
  `--state-dir`; there is deliberately no implicit real-state default, so a
  careless invocation can never write the user's live `~/Gaia/state`.

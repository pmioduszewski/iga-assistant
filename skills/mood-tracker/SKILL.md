---
name: mood-tracker
description: Personal mood / emotion tracker — a self-contained substrate + a derived contribution-grid widget. Imports a source-app CSV export (round-trip, anti-lock-in), maps every emotion to the mood-meter quadrant (valence × energy), and exposes a deterministic Iga-facing digest so the assistant can reason about the user's psychology for coaching. Stdlib only; the app just renders the data file it emits.
intent_triggers:
  - mood
  - emotion
  - feeling
  - mood tracker
  - mood-tracker
prerequisites:
  - name: python3
    description: stdlib-only Python 3; no third-party deps.
    check: cmd(python3)
    severity: error
triggers:
  - kind: cli
    spec: "`engine/record.py --state-dir ~/Gaia/state --emotion <name> --at <YYYY-MM-DDTHH:MM> [--note .. --people .. --places .. --events ..]` is the sanctioned chat log seam (the live, one-place answer). `engine/widget_projection.py [--days N]` rebuilds the Mood grid. `engine/summary.py` is the read-only Iga digest. `engine/import_mood_csv.py --input <csv> --state-dir <dir>` imports a mood-app export. `engine/ingest.py --state-dir ~/Gaia/state` is the idempotent backfill (newest export in the configurable watch folder iff changed)."
substrate:
  - kind: mood-tracker
    version: 1
    path: state/substrates/mood-tracker.json
    entities: mood-entry
    importer: engine/import_mood_csv.py
    exporter: engine/export_mood_csv.py
    derived:
      - widget:mood-grid
mempalace_wings:
  - iga/tooling/mood-tracker
mcp_dependencies: []
status: beta
---

# Mood Tracker — psychology context for Iga's coaching

The second substrate instance (after `habit-tracker`). Same architecture,
same guarantees: a self-contained, `$IGA_STATE_DIR`-isolated, atomically
written local store; a derived widget the menu-bar app renders without any
injected code; a deterministic Iga read path. The point is **coaching with
real psychological context** — Iga sees the user's valence/energy trend and
what co-occurs with the rough days, not just a grid she's blind to.

## Data model

`engine/substrate.py` — `MoodEntry`: civil day + local time, emotion
(display + canonical key), the **mood-meter quadrant / valence / energy**
(derived deterministically by `engine/quadrant.py` — the RULER framework
the source app uses: yellow=high-energy-pleasant, green=calm-pleasant,
red=high-energy-unpleasant, blue=low-energy-unpleasant), people/places/
events tags, the free-text note, and an `attrs['src']` round-trip bag.

## Importer / exporter (anti-lock-in)

```
uv run python skills/mood-tracker/engine/import_mood_csv.py \
    --input <mood-export.csv> --state-dir <DIR>   # --state-dir REQUIRED
uv run python skills/mood-tracker/engine/export_mood_csv.py \
    --state-dir <DIR> [--output out.csv]
```

- **Importer** maps every source-app column; idempotent (stable per-row
  id → re-import updates in place, never duplicates); **lossless** (the
  source row is kept verbatim in `attrs['src']`, so `import(export(S))`
  data-equals `S` — an exact round-trip fixpoint). `--state-dir` is
  **mandatory**: no implicit real-state default; a careless run can never
  clobber the user's live `~/Gaia/state`. No engine source hard-references
  the real export path (privacy guard, tested).
- **Exporter** rebuilds a source-app-format CSV; never writes the state
  tree. The user is never locked in.

## Widget (the Board section)

`engine/widget_projection.py` emits `state/widgets/mood-tracker-mood.json`
as a **schema_version-2 `mood-grid`** payload. It is a first-class custom
widget (exactly like the multi-habit Habits widget — NOT a generic
`widgets:`-frontmatter contribution-grid): the app's dedicated
`MoodWidgetView` + `MoodWidgetStore` read this file directly and render
the **Mood** Board section as a dense, mood-meter-coloured calendar —
fixed small cells, 7 weekday rows, horizontal scroll, newest-first —
where each day's tile is painted with that day's **dominant emotion's
mood-meter quadrant colour** (`palette` / `qcells[].color`, the same
four colours the source app uses), NOT a valence ramp. The payload
also keeps a legacy 0..4 `cells` array (so any generic contribution-grid
reader still works) plus a short deterministic coach line (dominant
quadrant · top emotion · trend). The view is strictly read-only; logging
happens via the `engine/record.py` chat seam, never the grid.

## Iga read path (the whole point)

`engine/summary.py` — sanctioned, read-only, deterministic,
`$IGA_STATE_DIR`-isolated digest (reuses the frozen `stats.summarize`; no
LLM; no clock except `--today`). Markdown for `/gm` + chat, `--json` for
tools: window logs/streak, dominant quadrant, valence/energy + trend, top
emotions, the context that co-occurs with rough logs (to explore, not
blame), last entry, and a gentle-coaching cue when the trend is declining.

```
IGA_STATE_DIR=~/Gaia/state uv run python \
  skills/mood-tracker/engine/summary.py [--today YYYY-MM-DD] [--days N] [--json]
```

Iga reads this (directly or shelled from `/gm`) and coaches FROM it —
never guesses the user's emotional state, never moralises about the
correlations (they're context, not causation).

## Logging a mood from chat — the live, one-place answer (binding)

The source mood app keeps its data in a private CloudKit container, so there is
**no silent auto-sync** (only the semi-automatic export → `ingest.py`
path). The real "track mood live without opening another app" answer is
this seam: when the user **expresses a feeling in chat** ("I'm anxious
about the demo", "felt great after the run with the kids"), Iga logs it
for them via the sanctioned record seam — exactly analogous to the habit
record seam. The grid/UI is render-only and **never** mutates.

```
uv run python skills/mood-tracker/engine/record.py \
  --state-dir ~/Gaia/state \
  --emotion "Anxious" --at 2026-05-17T14:30 \
  --note "before the demo" --people "Boss" --events "Deadline"
```

Behavioral contract for Iga:

- When the user clearly states an emotion, **offer to log it** (or log it
  if they've said to just track it); pick the closest single emotion word
  for `--emotion` (`;`-separate if they name several). Quadrant/valence/
  energy are derived deterministically by the engine — Iga never sets
  them.
- `--at` is the user's local civil timestamp **now** (Iga supplies it; the
  engine reads no clock). `--state-dir` is ALWAYS `~/Gaia/state`.
- Map context they mention into `--people/--places/--events` (comma
  lists); put the verbatim phrase in `--note` only if they'd want it kept.
- It is idempotent (same emotion+note+minute = no-op) and re-emits the
  Mood grid immediately, so the Board updates without any app action.
- Do **not** quote the note back unsolicited, and never infer a feeling
  the user didn't actually express — ask if ambiguous.

This + the read path (`summary.py`) is the closed loop: the user lives in
the "boring chat", Iga logs and later coaches from the same place.

## Privacy (binding)

Mood notes/reflections/takeaways are deeply intimate. Real data lives
ONLY in the gitignored `~/Gaia/state`. Every test is synthetic. The
exporter never writes the state tree; the importer requires an explicit
`--state-dir`; no engine source hard-references the real export path. OSS
ships zero personal emotion data, ever.

## Running the tests

```
cd <repo-root> && uv run python -m pytest skills/mood-tracker/tests/ -q
```

Covers the quadrant map + palette, the import field-mapping + idempotency
+ date parse, the round-trip **fixpoint** (`import(export(S))` data-equals
`S`) — including for **seam-authored** (chat-logged) entries, the
deterministic stats/summary, the dense `mood-grid` projection (per-day
dominant quadrant + colour), the record seam (importer-equivalent
modelling, idempotency, non-mutating `--reproject`), and the isolation +
privacy guard (no real export path referenced; `--state-dir` mandatory;
real `~/Gaia/state` byte/mtime-unchanged across the whole pipeline incl.
record).

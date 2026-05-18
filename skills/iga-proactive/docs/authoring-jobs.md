# Authoring a proactive job

This is the doc for a **skill author** who wants their skill to do background
work via the generic engine. You declare *what* should happen; the engine
guarantees it happens exactly once per idempotency key, under one global
budget, and surfaces the result.

## How the engine discovers your job

On every scan tick `runtime.discover_job_sources()` scans
`skills/*/` for two files, in deterministic sorted order:

1. `skills/<your-skill>/SKILL.md` — a `proactive:` list in the YAML
   frontmatter.
2. `skills/<your-skill>/proactive.yaml` — a bare YAML doc whose top-level key
   is `proactive:` (the runtime wraps it in a `--- ... ---` fence and reuses
   the same parser).

**Silent skip is not an error.** A skill with no frontmatter, or frontmatter
without a `proactive:` key, is simply *not a proactive skill*. It is skipped
silently and counted in `skipped_non_proactive` — it never lands in `errors`
and never shows red in the menu-bar app. Only a `proactive:` block that **is
present but fails to parse/validate** is a real error (recorded in `errors`,
that one skill skipped, the tick continues — one bad skill never aborts the
scan).

## The `proactive:` schema (full reference)

`proactive:` is a list of job mappings. One `Job` per list item. Parsed and
validated by `engine.schema.parse_jobs`.

| field | required | type | meaning |
|---|---|---|---|
| `id` | **yes** | string | Unique job id (unique across the whole skill; duplicate ids in one file → `SchemaError`). Non-empty. |
| `trigger` | **yes** | string | One trigger expression. See "Trigger kinds" below. Stored raw + parsed into `kind` + raw `args`. |
| `condition` | no | string | A predicate evaluated per candidate by a tiny `eval`-free language (see "Conditions"). Omit for no gating. |
| `action` | **yes** | string | What to do, e.g. `spawn_worker(prompt: engine/worker.prompt.md, depth: deep)`. Stored raw + parsed into `name` + raw `args`. The engine does not execute it — it surfaces it in the WORKER_REQUEST for the entrypoint to act on. |
| `idempotency_key` | **yes** | string | A template, kept verbatim by the schema. `{{ a.b }}` placeholders are rendered per candidate by `runtime.render_template`. This string is the ledger PRIMARY KEY — design it to be the exact identity of "this unit of work". |
| `budget` | no | mapping | Drives governor accounting. Keys: `model` (default `claude-opus-4-7[1m]`), `est_tokens` (explicit token estimate), or `wall_min` (derived: `wall_min * 10_000` tokens). `est_tokens` wins over `wall_min`; absent → 200 000 default. |
| `deliver` | no | enum | One of `surface_next_brief` (default), `slack_dm`, `todoist_comment`, `push`, `interrupt`. Carried through to the WORKER_REQUEST; the entrypoint honours it. Any other value → `SchemaError`. |
| `cooldown` | **yes** | duration | How long the ledger holds the `claimed`/terminal row for a given rendered key before the same key may be claimed again. Must be **positive**. |

### Duration syntax (`cooldown`)

Parsed by `schema.parse_duration_to_seconds`. Accepts:

- A composite string: `48h`, `7d`, `1h30m`, `2w`, `90m`, `1w2d3h`. Units:
  `w` weeks, `d` days, `h` hours, `m` minutes, `s` seconds. (`m` is minutes;
  `ms` is rejected — the regex guards `m(?!s)`.)
- A bare integer or int-as-string → already seconds (`3600`, `"3600"`).
- Zero / negative / unparseable / a bool → `SchemaError`.

### Idempotency-key templating

`render_template` substitutes `{{ key }}` from the candidate's render
namespace. Whitespace inside the braces is tolerated. **A missing key renders
as the empty string** — the candidate still gets a deterministic (if coarser)
key and the ledger still dedups it; it does not raise.

Available namespace keys depend on the trigger kind (always present:
`trigger.kind`, `source.id`, `candidate.title`):

| Trigger | Extra namespace keys |
|---|---|
| `todoist` | `task.id`, `task.title`, `task.due`, `task.context`, `task.label` |
| `mempalace` | `drawer.id`, `drawer.title`, `drawer.room`, `drawer.target_date`, `drawer.context` |
| `schedule` | `schedule.cron`, `schedule.tick` (minute-resolution UTC stamp) |
| `manual` | `manual` (`"true"`) |

Design the key so two scans of *the same real thing* render the *same* string
(that is what makes dedup exact). For Todoist research the proven shape is
`research::{{task.id}}::{{task.due}}` — task identity plus its due date, so a
rescheduled task is correctly treated as new work.

## Trigger kinds

`trigger` is exactly one expression. A `Job.trigger` holds one trigger — if
you need two sources, write two jobs sharing one worker prompt (this is what
the research port does).

| Kind | Form | Behaviour |
|---|---|---|
| `schedule` | `schedule(<5-field cron>)` | Fires one candidate iff the current tick matches the cron (`min hour dom month dow`; numeric only, `*`, `*/n`, `a-b`, `a-b/n`, `a,b,c`; Sunday is `0` or `7`). `source_id` is the minute stamp so re-ticks of the same minute dedup for free. |
| `todoist` | `todoist(label:X, due:<Nd)` | One candidate per open Todoist task carrying `label:X`. `due:<Nd` filters to tasks due/deadline within N days and not overdue. No token → `[]` (graceful). |
| `mempalace` | `mempalace(room:X)` | One candidate per drawer in room `X` whose `metadata.triggered` is not truthy. MemPalace unavailable → `[]` (graceful). |
| `manual` | `manual` | Always one eligible candidate. The ledger cooldown + governor still gate it, so it does not fire-loop. |
| `calendar` | `calendar(window:Nh)` | **Wave 3 stub.** Parses fine; raises `NotImplementedError` at eval. The runtime catches it, logs, and skips *that one job* — the tick continues. Interface is fixed for a future calendar fetcher. |
| `watch` | `watch(<predicate>)` | **Wave 3 stub.** Same as `calendar`: parses, raises at eval, runtime skips that job only. |

If your skill uses `todoist`/`mempalace`/`schedule`/`manual` you are fully
supported today. `calendar`/`watch` will parse and validate but will not
produce candidates yet — do not depend on them.

## Conditions

`condition` is an optional, deliberately tiny, `eval`-free predicate
(`runtime.eval_condition`) evaluated against the candidate namespace:

- omitted / empty / `manual` → `True` (no gating)
- `<key> exists` → key present and non-empty
- `<key> not exists` / `not exists drawer for task` → conservatively `True`
  (a real "no drawer exists" check is the worker/trigger layer's job, not a
  flat-namespace predicate — **fail-open**)
- `<key> == <value>` / `<key> != <value>` → string compare
- `<key> contains <substr>` → substring test
- `<value> in <key>` → membership test
- anything unparseable → `True` with a logged warning

**Fail-open is intentional and safe**: the ledger and governor are the real
guards, so a mis-written condition can only let a candidate *through* to those
hard gates — it can never cause a duplicate spawn or a budget breach.

## Worked example (copy-pasteable)

This is the real declarative research job
(`skills/iga-proactive-research/proactive.yaml`), verbatim. It is a bare YAML
doc (not SKILL.md frontmatter); the runtime wraps it in a fence automatically.
Two jobs because Todoist and the MemPalace research-queue are two sources, and
one `Job.trigger` holds exactly one trigger — they share one worker prompt.

```yaml
proactive:
  - id: research-todoist
    trigger: todoist(label:iga-research, due:<7d)
    condition: not exists drawer for task
    action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)
    idempotency_key: research::{{task.id}}::{{task.due}}
    budget:
      model: claude-opus-4-7[1m]
      wall_min: 30
    deliver: surface_next_brief
    cooldown: 48h

  - id: research-mempalace-queue
    trigger: mempalace(room:research-queue)
    condition: not exists drawer for task
    action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)
    idempotency_key: research::{{drawer.id}}::{{drawer.target_date}}
    budget:
      model: claude-opus-4-7[1m]
      wall_min: 30
    deliver: surface_next_brief
    cooldown: 48h
```

Notes that matter for an author:

- `prompt:` paths in `action` resolve **relative to the source file's
  directory** (`dispatcher.extract_prompt_path` with `base=` the skill dir).
  Here `engine/worker.prompt.md` resolves to
  `skills/iga-proactive-research/engine/worker.prompt.md`. Use a path that
  exists relative to your skill.
- The same task scanned twice within `cooldown` renders the same key, the
  ledger already holds a live `claimed` row → exactly one WORKER_REQUEST.
  That is the anti-duplicate guarantee, end to end.
- This YAML is the **upstream/generic** layer. User-specific tuning (extra
  labels, cooldown overrides) belongs in a `.local` override per the repo's
  composability contract — never edit the shipped file for personalization.
  See `docs/oss-publishing.md`.

## Quick validation while authoring

```sh
cd skills/iga-proactive
PYTHONPATH=engine uv run python -c "import schema, pathlib; \
print(schema.parse_jobs(pathlib.Path('../<your-skill>/proactive.yaml').read_text()))"
```

(or wrap a SKILL.md text and pass it the same way — `parse_jobs` accepts the
raw SKILL.md string and pulls the block out of the frontmatter). Then dry-run
the full tick to see what *would* queue:

```sh
PYTHONPATH=engine uv run python -m engine scan --dry-run --json
```

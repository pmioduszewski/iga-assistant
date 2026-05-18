# newsletter-research

Iga skill that extracts R&D artifacts (libs / repos / tools / blog posts)
from labeled `Newsletter/Dev` + `Newsletter/Business` mail, fit-scores them
against your active projects, and files high-fit findings to the MemPalace
Knowledge Vault for later surfacing.

See `SKILL.md` for the LLM-facing spec and the **Killswitch** section (this
skill is OFF by default).

## How it mirrors `iga-proactive-research`

Identical proven structure — a declarative job the generic
`skills/iga-proactive` engine discovers, deterministic stdlib helpers, a
single-shot worker prompt, unit tests:

```
skills/newsletter-research/
  SKILL.md              ← spec + frontmatter (widgets:, prereqs, Killswitch)
  proactive.yaml        ← ONE job the generic engine discovers (analogous to
                          iga-proactive-research/proactive.yaml)
  engine/
    extract.py          ← deterministic helpers (artifact-extraction
                          scaffolding, project-fit scoring, dedup keying,
                          output contract). NO LLM, NO I/O.
    worker.prompt.md    ← single-shot worker system prompt (the LLM half)
  tests/
    test_extract.py            ← pure-function tests
    test_engine_discovery.py   ← engine discovers it + safety-gate proof
    conftest.py
  README.md             ← this file
```

The split is the same as the research port: **the engine never calls an
LLM** — it detects, dedups, budget-gates, and emits a worker request; the
**worker** does the reading and judgement.

## The job (`proactive.yaml`)

| field | value |
| --- | --- |
| `id` | `newsletter-research-queue` |
| `trigger` | `mempalace(room:newsletter-research-queue)` — deterministic, testable; NOT a live email-label poll |
| `condition` | `not exists drawer for task` (fail-open; mirrors the research port verbatim) |
| `action` | `spawn_worker(prompt: engine/worker.prompt.md, depth: shallow)` |
| `idempotency_key` | `newsletter::{{drawer.id}}::{{drawer.target_date}}` |
| `budget` | `claude-opus-4-7[1m]`, `wall_min: 20` |
| `cooldown` | `72h` (ledger anti-duplicate guard) |

## Safety gate (OFF by default — see SKILL.md § Killswitch)

The generic engine **discovers** the job (parses, validates, shows in a
scan) but **spawns nothing unattended**: the trigger is a MemPalace room
poll and the `newsletter-research-queue` room is **empty by default → zero
candidates → zero workers**. The empty room *is* the killswitch — the exact
safety property `iga-proactive-research`'s `research-mempalace-queue` job
relies on. Engine-wide `IGA_PROACTIVE_SPAWN=0` is the belt-and-braces global
suppressor.

the user arms it by filing one flag drawer into that room (full procedure in
SKILL.md). No code edit flips it either way — the gate is data.

## Board surface (zero Swift)

`SKILL.md` declares a `widgets:` block (`type: message`,
`data_source: ~/Gaia/state/widgets/newsletter-research-findings.json`). The
generic menu-bar `WidgetHost` (`SkillDiscovery` + `WidgetHostStore` +
`WidgetHostView`) already discovers and renders `message` widgets read-only
and tolerates an absent data file (shows "waiting for newsletter-research").
**No app code is specific to this skill** — minimal footprint, contract
intact (no Process / record / engine seam from the card). The worker writes
the findings JSON atomically each run.

## Tests

```bash
cd <iga-assistant>/skills/newsletter-research
python3 -m pytest tests/ -q
```

`test_engine_discovery.py` imports the real `skills/iga-proactive` engine
read-only and asserts (1) the engine parses `proactive.yaml` with no schema
error, (2) with the queue room empty a real `scan_tick` queues NOTHING for
this skill (the safety gate), and (3) arming one flag drawer queues exactly
one gated worker.

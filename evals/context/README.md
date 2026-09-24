# Context-retrieval benchmark

Measures the step **before** the assistant answers: given a situation (a user
message, a tool call about to happen, or a tool error), which memory drawers
reach the context? Wrong or missing context is the root of "the assistant knew
this and still got it wrong" failures, so it is measured on its own, without
running the assistant.

Everything here is **synthetic**: a fictional persona (a landscape architect
running a small studio) with a 157-note palace and hand-labelled cases. No real
person, project or account appears in it. Keep it that way when adding cases.

## What a case checks

Each case in `fixtures/cases.json` has a trigger and two labels:

| field | meaning |
|---|---|
| `must` | notes the assistant needs to act correctly (the current truth) |
| `must_not` | stale or look-alike notes that mislead if read as current |

A case is **solved** when every `must` note (or a newer note that supersedes
it) is in context. A **leak** is a `must_not` note in context.

Planted patterns (`fixtures/manifest.json`): status changed with and without a
supersede link, rules that only matter for an action (the keywords are in the
tool arguments, not the message), tool quirks that later get fixed, corrections
written only as prose, look-alike decoys, facts buried in long multi-topic
notes, and the same rule filed twice.

`fixtures/cases_hard.json` is the same 44 cases with the messages rewritten the
way a busy person types ("did the park thing ever wrap up"), with almost no word
overlap with the notes they need.

## Strategies

| name | what it does |
|---|---|
| `names` | the name-based guard: entities found in the message, top note each (needs the palace-guard skill) |
| `semantic` | search on the meaning of the whole situation (message + tool call or error), top k |
| `judge` (`--judge`) | `semantic` candidates filtered by the provider's cheap model |

`--link` replays every note as if it had just been saved and runs the
**save-time linker** (`linker.py`): the cheap model marks older notes that the
new one makes outdated, so later searches stop returning them. A link needs the
model to quote the old and new fact word for word (checked in code), never
retires a rule because the reason behind it went away, never retires journal
notes, and only retires a long note when the changed facts are most of it.
Links that hide a `must` note are reported as `hidden`.

## Run

Needs Python 3.10+ with a MemPalace build that has supersedence, and for
`--judge` / `--link` a configured `iga_llm` provider (see `iga_llm/README.md`).

```bash
python evals/context/run.py                                   # names vs semantic
python evals/context/run.py --cases-file cases_hard.json --k 10
python evals/context/run.py --strategies semantic --link --link-model haiku \
    --link-cache /tmp/link-cache.json                         # cached reruns are free
python evals/context/run.py --verbose --cases c01,c02         # per-case detail
```

The palace is built in a temp directory on every run. The runner refuses to
start if the palace path does not resolve to that temp directory, so a real
palace set through `MEMPALACE_PALACE_PATH` is never touched.

## Results so far (2026-09-24, Claude Haiku as the cheap model)

| strategy | solved | leaks | per check |
|---|---|---|---|
| names | 2% | 2% | 0.02 s |
| semantic, k=5 | 93% | 14% | 0.02 s |
| semantic + judge | 95% | 0% | 12-60 s (CLI) |
| semantic + save-time linker, k=5 | 91% | 2% | 0.02 s |
| semantic + save-time linker, k=10 | 98% | 2% | 0.02 s |
| hard wording, linker, k=5 / k=10 | 66% / 82% | 2% / 5% | 0.02 s |

Takeaways: search on the meaning of the whole situation beats name matching by
a wide margin; stale leaks come from missing supersede links, so the model
belongs at save time (slow, rare) rather than at read time (fast, every
message). Indirect messages are the open problem. The linker's long-note
threshold was chosen on this set, so confirm it on new cases before trusting it.

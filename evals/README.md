# Iga Eval Harness

A regression test suite for [Iga](https://github.com/pmioduszewski/iga-assistant), the personal AI assistant. The core failure it targets: **Iga retrieving stale facts from MemPalace and surfacing them as actionable items.**

Canonical example: Iga keeps flagging a stale "renew the old-brand domain" item in daily briefings months after a rebrand decision drawer said *let the old domain lapse*. The harness reproduces this pattern in anonymized form so we can prove a fix works and catch future regressions.

---

## Quick start

This project uses **pnpm**, not npm. (`packageManager` is pinned in `package.json`.)

```bash
cd evals
pnpm install
cp .env.example .env          # then edit .env and add ANTHROPIC_API_KEY
pnpm test                     # runs vitest (parser + scenarios)
```

`pnpm test` runs:

- **Parser tests** (always) — pure unit tests over the judge output parser.
- **Scenario tests** (if `ANTHROPIC_API_KEY` is set) — live calls. Each scenario boots Iga-under-test (Sonnet), captures the response, ships it to the judge (Opus), and asserts the binary outcome.

Optional explorer mode:

```bash
pnpm run promptfoo            # promptfoo eval -c promptfooconfig.yaml
```

Other scripts:

```bash
pnpm run test:judges          # parser tests only
pnpm run test:scenarios       # scenarios only
pnpm run mock-mcp             # stdio MCP stub (not wired into vitest path)
pnpm run calibrate            # Iga-driven calibration stub (see below)
```

**Lockfile:** only `pnpm-lock.yaml` is committed. `package-lock.json` and `yarn.lock` are gitignored — if you ever see one appear locally, delete it.

---

## Architecture decisions and citations

| Decision | Why | Source |
|---|---|---|
| **Promptfoo + vitest** (not Braintrust/LangSmith) | OSS-friendly, YAML test cases, no SaaS lock-in. | — |
| **Judge: Claude Opus 4.7**, system-under-test: **Claude Sonnet 4.6** | Don't judge a model with itself. Stronger judge catches subtle drift in the SUT. | Hamel Husain, [Creating an LLM-as-a-Judge That Drives Business Results](https://hamel.dev/blog/posts/llm-judge/) |
| **Critique-then-binary** judge pattern (not Likert) | Binary outcomes correlate with human judgements; the critique exists for debugging, not scoring. | Hamel, [LLM-as-Judge](https://hamel.dev/blog/posts/llm-judge/) ("Honeycomb template") |
| **Hand-crafted synthetic fixtures**, no real user data | Repo is public; real PII / company-specific traces cannot ship. | Project policy |
| **Mock MCP server + inline fixtures** (not pre-injected context) | Pre-injection bypasses Iga's real failure mode (failing to call the tool). Inline fixtures inside the model context still force the model to reason over them as "retrieved" content. | Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) |
| **Contextual-retrieval framing** of the failure | The bug is a retrieval+grounding failure; framing matches the upstream literature on doc-level reranking. | Anthropic, [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) (cites -67% retrieval failure) |
| **Iga-driven chat calibration** (not offline hand-labeling) | Primary user can't do offline batch annotation. Iga surfaces contested cases one at a time via AskUserQuestion. | Project constraint; see "Calibration" below |

---

## What's in the box

```
evals/
  package.json                vitest + promptfoo + Anthropic SDK (pnpm)
  pnpm-lock.yaml              committed; npm/yarn lockfiles forbidden
  vitest.config.ts            120s test timeout (judge calls are slow)
  promptfooconfig.yaml        optional ad-hoc mode
  .env.example                ANTHROPIC_API_KEY + model overrides
  fixtures/
    drawers/
      baseline.json           10 generic synthetic drawers (Alex Rivera persona)
      brand-rebrand.json      Pipevine → Solera rebrand pattern
      customer-correction.json Steamcraft Engineering correction
    mcp-responses/
      gmail/                  triage outputs with/without the stale renewal email
      todoist/                baseline tasks
      calendar/               empty-ish day
  mocks/
    mcp-server.ts             stdio MCP stub (not wired into vitest path, see below)
    README.md                 how to use it with `claude -p --mcp-config`
  judges/
    relevance-judge.md        general-purpose Honeycomb-style judge
    staleness-judge.md        specialist judge for the stale-fact failure mode
  scenarios/
    brand-rebrand-suppressed.yaml      *** the canonical regression ***
    customer-correction-respected.yaml
    legit-deadline-surfaced.yaml       positive case (over-suppression guard)
  src/
    types.ts                  JudgeResult, Scenario, IgaRunResult
    testSetup.ts              dotenv loader
    runJudge.ts               Anthropic SDK Opus call + tag parser
    runScenario.ts            loads scenario, runs SUT, formats transcript
    calibrateInChat.ts        stub: Iga-driven chat calibration loop
  calibration/
    contested-cases.jsonl     the user's verdicts on contested cases (ground truth)
  test/
    judges.test.ts            parser unit tests + calibration scaffold
    scenarios.test.ts         live end-to-end scenarios
```

---

## Fixture personas

All names are fictional but **chosen to avoid placeholder-bias in LLM judges/SUTs**. Names like "Acme", "Foo Corp", "Northwind Industries" appear constantly in training data as throwaway test placeholders; an LLM seeing them will partially shift into "this is fake test data" mode, which corrupts both the SUT's reasoning and the judge's verdict. Real-sounding fictional names keep the model in the same reasoning mode it would use on actual content.

| Persona / entity | Role in fixtures | Notes |
|---|---|---|
| **Alex Rivera** | Synthetic primary user | Common name, low placeholder vibe — kept |
| **Mira Kovac** | Co-founder, lead designer (Lisbon) | Fictional but plausible |
| **Dan Howe** | Infra/devops contractor | Fictional |
| **Jana Park** | Investor, partner at Northstar Ventures | Fictional |
| **Pipevine** | Deprecated old brand name (rebrand pattern) | Replaces "Acme" — sounds real, not throwaway |
| **Solera** | Current brand name (rebrand pattern) | Replaces "Beacon" — same |
| **pipevine.example** / **solera.example** | Old / new domains for rebrand scenario | `.example` TLD by RFC 2606 |
| **Steamcraft Engineering** | Incumbent ERP vendor at the prospect — **NOT** a customer | Replaces "Northwind Industries"; industrial-feeling, avoids the Microsoft-demo prior |
| **Mercia Industrial** | Active prospect, currently runs on Steamcraft | Replaces "Helios Group"; UK-industrial feel |
| **Yuki Tanaka** | COO at Mercia Industrial; primary prospect contact | Fictional |

Real-world MemPalace entities are never named in this repo. Fixtures use abstract synthetic personas (above) whose roles correspond loosely to common categories: a primary user, a rebrand pair (deprecated brand → current brand, with paired domains), a "wrong customer / right prospect" correction pair, and a generic investor relationship. Contributors should map any real entity in their own data to an abstract role rather than naming it.

**If you contribute a new fixture, you must use synthetic data and add the persona here.** Avoid Acme, Foo, Bar, Northwind, Contoso, Initech, Hooli — all heavy placeholder priors.

---

## The "currently failing" scenario

`scenarios/brand-rebrand-suppressed.yaml` is **expected to FAIL** on the current Iga. That's the entire point — it's the regression test for **move #1** of the bigger plan (introducing supersedence edges into MemPalace so OBSOLETE drawers can no longer outrank live ones in retrieval).

The vitest runner uses `it.fails` when it sees `expected_status: currently_failing ...` in the scenario frontmatter. A green run on a `currently_failing` scenario is treated as a regression — once the fix lands and the scenario starts passing, drop the `expected_status` line so it becomes a normal positive test.

---

## Calibration (the Iga-driven loop)

**The user does NOT hand-label.** Offline batch annotation is not a workflow that fits them. Iga (the assistant) drives calibration via conversation:

1. Iga runs the scenario suite to generate N candidate transcripts.
2. Iga self-labels each transcript using the current judge prompt + her own judgment, attaching a confidence score `[0, 1]`.
3. For any transcript where Iga's confidence is below **0.85** OR her self-label disagrees with the judge's verdict, she surfaces it to the user via `AskUserQuestion` in chat — **one transcript at a time**, with the transcript + critique inline.
4. The user answers pass / fail / "skip — ambiguous, exclude from calibration set".
5. The user's verdicts on contested cases become ground-truth labels.
6. Iga iterates the judge prompts in `judges/*.md` until self-label agreement with the user's contested-case ground-truth reaches **≥90%** on a held-out subset.
7. Locked judge prompts get committed; calibration ground-truth lives in `evals/calibration/contested-cases.jsonl`.

The flow lives in `src/calibrateInChat.ts` (stub today — wired once supersedence ships and Iga has the tool surface to drive AskUserQuestion from inside an eval session).

**Hand-labeling is a fallback only.** Contributors comfortable with offline batch annotation can still hand-label and drop entries directly into `calibration/contested-cases.jsonl` if they prefer — but the docs describe the chat-driven workflow as canonical to keep that UX as the first-class path.

---

## Why not `claude -p` for the SUT?

The first cut of this harness called `claude -p` with `--mcp-config` pointing at the mock MCP server. It was brittle: shelling out, parsing CLI output, juggling environment, race conditions on stdio. Worse, the regression we care about (stale-fact reasoning) lives in **how the model reasons over retrieved context**, not in Claude Code's prompt-assembly layer.

So `src/runScenario.ts` calls the Anthropic SDK directly with:

- A compact Iga system prompt (TL;DR style, MemPalace-grounded, surface-only-actionable rules).
- The drawers inlined as a `<memory>` block.
- The MCP fixtures inlined as a `<tools_state>` block.

The mock MCP server (`mocks/mcp-server.ts`) is still here, ready to be wired into a future `claude -p` integration test. For now: inline fixtures = deterministic, fast, debuggable.

---

## Two tiers of scenarios

The harness runs in two complementary tracks:

| | Tier 1 (`scenarios/`) | Tier 2 (`scenarios-tier2/`) |
|---|---|---|
| What it tests | Iga's *reasoning* over already-retrieved context | Iga's *decision* to call tools at all, plus reasoning |
| How context arrives | Inlined as `<memory>` / `<tools_state>` blocks in the prompt | Iga calls fake tools; mock router answers from fixtures |
| Catches | Surfacing OBSOLETE drawers, ignoring supersedence | Skipping `mempalace_search` entirely; over-calling on trivial requests; query-expansion failures |
| Asserts on | Final assistant message via Opus judge | Tool-call sequence (`must_call`, `must_not_call`, `must_call_before`, args matching) PLUS judge |
| Run | `pnpm test:tier1` | `pnpm test:tier2` |
| Both | `pnpm test` (== `pnpm test:all`) | |

Why both: pre-injecting retrieved memory (tier 1) bypasses exactly the failure
mode where Iga never decides to search the palace before classifying something
as actionable. Tier 2 forces her to drive the loop herself; the mock router
(`mocks/mcpToolRouter.ts`) intercepts each `tool_use` block, picks a matching
fixture via `mocks/fixtureLoader.ts`, and returns deterministic data.

Tier 2 plugs into the **same** Iga-driven calibration loop described above —
contested judge outcomes get surfaced to the user via `AskUserQuestion`, his
verdicts append to `CALIBRATION_GROUND_TRUTH`, and the judge prompt iterates
until agreement crosses threshold. The only addition for tier 2 is that
*structural* tool-call assertions never reach the judge — they're pure code
checks in `checkAssertions()`, so calibration only ever touches the
prose-judging axis.

---

## Adding a new scenario

1. Drop a YAML file in `scenarios/` following the existing shape (`id`, `description`, `drawers`, `mcp_fixtures`, `user_message`, `judge`, `judge_criteria`).
2. Add any new drawer fixtures to `fixtures/drawers/` and MCP fixtures to `fixtures/mcp-responses/`. **Anonymize everything, and avoid placeholder-flavored names** (see "Fixture personas").
3. Pick the judge: `relevance` for general grounding/style, `staleness` for the stale-fact failure mode.
4. Run `pnpm test`. If you expect the scenario to fail today (e.g. a new regression target), set `expected_status: "currently_failing — ..."`.

---

## What's deferred

- Live `claude -p` end-to-end via the mock MCP server. Stub exists, not wired.
- Full implementation of `calibrateInChat.ts` (waiting on supersedence + AskUserQuestion-from-eval surface).
- Cost dashboard / per-run token accounting.
- Multi-turn scenarios (current ones are single-turn).
- A larger fixture corpus for retrieval-recall metrics (the Anthropic Contextual Retrieval paper's setup).

---

## License

MIT, same as the parent repo.

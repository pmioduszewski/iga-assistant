# iga-assistant

A personal AI assistant that runs **inside an agent harness you already have**, not a standalone app: [Claude Code](https://claude.com/claude-code) (the reference harness) or [Codex CLI](https://github.com/openai/codex) (newer, see [Harness support](#harness-support)). Iga is a composable substrate of **skills**, **rules**, and a persistent memory palace (**MemPalace**) that turns that harness into a life/projects orchestrator with real recall.

> Status: **early, single-maintainer, pre-1.0.** Public so the architecture and the `iga-assistant` namespace are out in the open. Expect sharp edges; APIs and pack layouts can still move.

### Naming

The assistant is **Iga** throughout — identity, brand, repo (`iga-assistant`), skills, docs, the macOS app, the command namespace (`/iga …`), the memory MCP (`IgaMemory`), and the engine identifiers/env vars (`IGA_*`). The home directory is `~/Iga`.

## What it actually is

Most "personal AI" projects ship a monolithic desktop app. Iga is the opposite bet: a **harness-coupled layer** you extend with packs.

- **Skills** (`skills/<name>/`) — capabilities Iga *does*: a workflow + optional engine code (Python/Swift). E.g. `mood-tracker`, `habit-tracker`, `iga-proactive`.
- **Rules** (`rules/<name>.md`, gitignored) — preferences for *how* Iga uses a tool. Generic baseline ships in `community_rules/`; personal overrides in `*.local.md` and never leave your machine.
- **MemPalace** — the memory layer: AAAK diary, knowledge graph, semantic recall. Iga without it is just a chatbot.
- **Composability contract** — `community_*` (upstream, MIT) → installed copy (provenance-stamped) → `*.local.md` (yours, gitignored). `/iga update` does a three-way merge so you can pull upstream improvements without losing personalizations.

See [`CLAUDE.md`](CLAUDE.md) (also published as `AGENTS.md`, the same file, so Codex reads it too) for the full operating contract and [`iga_memory_protocol.md`](iga_memory_protocol.md) for the memory model.

## Prerequisites — scoped by what you actually use

The stack is polyglot **by domain fit**, not accident. You only need the row for the capability you want:

| You want… | Need | Notes |
|---|---|---|
| Core assistant + skill engines | **`python3` ≥ 3.11** only | Engines are **stdlib-only, zero pip deps** — runs anywhere with system Python |
| A host harness | [Claude Code](https://claude.com/claude-code) CLI **or** [Codex CLI](https://github.com/openai/codex) | Conversational Iga runs inside one of them. Claude Code is the reference; see [Harness support](#harness-support) for what Codex lacks today |
| The `iga` MCP server (habit, mood, ask tools) | `python3` with `pip` **or** [`uv`](https://docs.astral.sh/uv/) | `scripts/setup-iga-mcp.sh` creates the venv and installs with `pip`, or with `uv` when the venv has none |
| MemPalace | the bundled `mempalace` venv | Set up once; see `iga_memory_protocol.md` |
| MCP integrations (Todoist, Calendar, Gmail, …) | **Node.js** ≥ 20 | Only the MCP servers that need it; configured per `.mcp.json` |
| The macOS menu-bar widget app | **macOS 14+ & Swift 6 / Xcode CLT** | Optional, Mac-only; **not** required for the core assistant |
| Contributing / secret-scanning hooks | [`ggshield`](https://github.com/GitGuardian/ggshield) | `brew install ggshield`; see below |

**Minimum to try it:** Claude Code or Codex CLI, plus `python3`. Everything else is additive.

## Quick start

```bash
git clone https://github.com/pmioduszewski/iga-assistant.git
cd iga-assistant

# 1. Enable the local secret guard (every clone — git doesn't auto-enable hooks dirs)
git config core.hooksPath .githooks
brew install ggshield        # or your platform's package manager

# 2. One-time wiring. Idempotent, asks before each step, supports --dry-run.
#    Builds the iga MCP venv and registers `iga` (+ `IgaMemory` if present) with
#    every harness it finds: Claude Code, Codex CLI, VS Code, Cursor.
#    For Codex it also links the admin commands as the `iga` skill.
scripts/setup-iga-mcp.sh

# 3. Open your harness in this directory (start a NEW session after step 2)
claude        # or: codex

# 4. In-session, check health and see what's installed
/iga status   # Claude Code
/iga status   # Codex desktop app (skills show up in the / menu)
$iga status   # Codex CLI (skills use the $ prefix there)
```

Install a community pack (same commands in Codex, as `/iga` in the desktop app or `$iga` in the CLI):

```
/iga install <pack>      # rule pack or skill bundle, shows contents first
/iga check-updates       # which installed packs have upstream changes
/iga update <pack>       # three-way merge, preserves your *.local.md
```

## Harness support

Honest state, per capability. "Unverified" means it runs but no eval has checked the behaviour on that model yet.

| Capability | Claude Code | Codex CLI |
|---|---|---|
| Identity + operating contract | `CLAUDE.md` | `AGENTS.md` (same file) |
| MemPalace + `iga` MCP tools | yes | yes, via `scripts/setup-iga-mcp.sh` |
| Admin commands | `/iga …` | `/iga …` in the desktop app, `$iga …` in the CLI (skill, same source of truth) |
| Assistant behaviour quality | covered by `evals/` | unverified |
| Personal overrides (`CLAUDE.local.md`) | auto-loaded | not loaded yet |
| Prompt hooks (time injection, recall nudges) | yes | time injection only: `scripts/setup-iga-mcp.sh` installs it, approve it once with `/hooks`; recall nudges not ported yet |
| `iga_ask` (persistent session tool) | yes | runs, but drives a Claude session underneath |
| Headless engines (email triage, research, proactive) | `IGA_PROVIDER=claude-cli` (default) | `IGA_PROVIDER=codex-cli` |
| Connected integrations (calendar, tasks, email) | whatever your harness has connected | same, but connectors injected by the Claude desktop app (Google Calendar, Todoist, …) are NOT available to Codex; add them as standalone MCP servers if you want them there |

Because the connected set differs per harness, the daily briefings (`/gm`, `/back`, `/eod`) run a preflight: any required source that is not reachable this session is reported as a `⚠️ <source> unavailable` line and its section runs degraded, rather than being silently dropped or papered over with an invented routine.

The headless engines go through [`iga_llm/`](iga_llm/README.md), a small provider entry point. `claude-cli` and `codex-cli` are the supported backends; `anthropic`, `openai` and `ollama` exist but are experimental (mock-tested only).

## Security & privacy

- **No secrets in the tree.** Credentials live in `~/.config/<svc>`, env vars, and the gitignored state dir (`$IGA_HOME/state`, default `~/Iga/state`). The repo ships **synthetic data only**.
- `.githooks/{pre-commit,pre-push}` run `ggshield` (same engine as the server-side GitGuardian check) **before** a commit object exists. Triaged false positives are documented per-entry in `.gitguardian.yaml` — the scanner is never disabled.
- `*.local.md` (personal rule overrides) and `state/` are gitignored and never published upstream.
- `.githooks/iga-guard.sh` adds an LLM privacy judge on every commit and push (personal data, not only secrets), and CI re-checks the agent-session-link rule server-side. `scripts/setup-iga-mcp.sh` turns the hooks on for you.

## Roadmap (honest — these are *intentions*, not shipped)

- **Codex parity.** Shipped so far: the `iga_llm` provider entry point, Codex MCP registration, the `iga` skill and the time hook (see [Harness support](#harness-support)). Still to do, in order: port the remaining prompt hooks (recall nudges) to Codex's native hooks, load personal overrides there, move the `iga` MCP session server off `claude --resume`, and run the evals on a second provider.
- **More headless.** Anthropic's 2026-06-15 billing split makes programmatic `claude -p` and Agent SDK paths metered, which is hostile to autonomous OSS use. Keeping every headless path behind `iga_llm` is what lets a user pick the cheapest backend they already pay for.

## How it compares

It does **not** try to be a 118-integration desktop app. If you want a self-contained Tauri assistant, projects like [openhuman](https://github.com/tinyhumansai/openhuman) are further along on that path. Iga's bet is different: **harness-native composability** (install/fork/update skill & rule packs inside Claude Code or Codex), **MemPalace recall quality**, and **contract-guarded native widgets**. Different shape, deliberately.

## License

[MIT](LICENSE). Personal layers (`*.local.md`, `state/`) are yours and never part of the distribution.

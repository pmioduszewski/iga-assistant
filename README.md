# iga-assistant

A personal AI assistant that runs **inside [Claude Code](https://claude.com/claude-code)** — not a standalone app. Iga is a composable substrate of **skills**, **rules**, and a persistent memory palace (**MemPalace**) that turns Claude Code into a life/projects orchestrator with real recall.

> Status: **early, single-maintainer, pre-1.0.** Public so the architecture and the `iga-assistant` namespace are out in the open. Expect sharp edges; APIs and pack layouts can still move.

### Naming & status (read this before you judge the `iga` you'll see)

The assistant was originally **Iga** and is being renamed to **Iga**. The rename is **deliberately staged, not finished**: the brand, repo, skills, docs and the macOS app are already `Iga`, but the internal command namespace is still `/iga …`, the memory MCP is still `IgaMemory`, and some engine identifiers/env vars still read `iga`. That's tracked, coordinated work — `/iga` is the *current, working* command and is treated as a legacy alias until the sweep lands. If you see `iga` in code/commands, it's mid-migration, not abandoned.

## What it actually is

Most "personal AI" projects ship a monolithic desktop app. Iga is the opposite bet: a **harness-coupled layer** you extend with packs.

- **Skills** (`skills/<name>/`) — capabilities Iga *does*: a workflow + optional engine code (Python/Swift). E.g. `mood-tracker`, `habit-tracker`, `iga-proactive`.
- **Rules** (`rules/<name>.md`, gitignored) — preferences for *how* Iga uses a tool. Generic baseline ships in `community_rules/`; personal overrides in `*.local.md` and never leave your machine.
- **MemPalace** — the memory layer: AAAK diary, knowledge graph, semantic recall. Iga without it is just a chatbot.
- **Composability contract** — `community_*` (upstream, MIT) → installed copy (provenance-stamped) → `*.local.md` (yours, gitignored). `/iga update` does a three-way merge so you can pull upstream improvements without losing personalizations.

See [`CLAUDE.md`](CLAUDE.md) for the full operating contract and [`iga_memory_protocol.md`](iga_memory_protocol.md) for the memory model.

## Prerequisites — scoped by what you actually use

The stack is polyglot **by domain fit**, not accident. You only need the row for the capability you want:

| You want… | Need | Notes |
|---|---|---|
| Core assistant + skill engines | **`python3` ≥ 3.11** only | Engines are **stdlib-only, zero pip deps** — runs anywhere with system Python |
| Claude Code itself | [Claude Code](https://claude.com/claude-code) CLI | The host harness; everything runs through it |
| MemPalace | the bundled `mempalace` venv | Set up once; see `iga_memory_protocol.md` |
| MCP integrations (Todoist, Calendar, Gmail, …) | **Node.js** ≥ 20 | Only the MCP servers that need it; configured per `.mcp.json` |
| The macOS menu-bar widget app | **macOS 14+ & Swift 6 / Xcode CLT** | Optional, Mac-only; **not** required for the core assistant |
| Contributing / secret-scanning hooks | [`ggshield`](https://github.com/GitGuardian/ggshield) | `brew install ggshield`; see below |

**Minimum to try it:** Claude Code + `python3`. Everything else is additive.

## Quick start

```bash
git clone https://github.com/pmioduszewski/iga-assistant.git
cd iga-assistant

# 1. Enable the local secret guard (every clone — git doesn't auto-enable hooks dirs)
git config core.hooksPath .githooks
brew install ggshield        # or your platform's package manager

# 2. Open Claude Code in this directory
claude

# 3. In-session, check health and see what's installed
#    (/iga is the current command namespace — legacy, rename to /iga in progress)
/iga status
/iga rules
```

Install a community pack:

```
/iga install <pack>      # rule pack or skill bundle, shows contents first
/iga check-updates       # which installed packs have upstream changes
/iga update <pack>       # three-way merge, preserves your *.local.md
```

## Security & privacy

- **No secrets in the tree.** Credentials live in `~/.config/<svc>`, env vars, and gitignored `~/Gaia/state`. The repo ships **synthetic data only**.
- `.githooks/{pre-commit,pre-push}` run `ggshield` (same engine as the server-side GitGuardian check) **before** a commit object exists. Triaged false positives are documented per-entry in `.gitguardian.yaml` — the scanner is never disabled.
- `*.local.md` (personal rule overrides) and `state/` are gitignored and never published upstream.

## Roadmap (honest — these are *intentions*, not shipped)

- **Harness-agnostic / more headless.** Today conversational Iga is coupled to Claude Code. Anthropic's 2026-06-15 billing split makes programmatic `claude -p`/Agent-SDK paths metered, which is hostile to autonomous OSS use. The plan: a small **provider-abstraction seam** over the headless paths so backends are swappable (Claude API, **Codex / GPT**, **Gemini**, local). Conversational use stays on whatever harness is cheapest. *Status: analysis done, direction not yet locked, seam not built.*
- Finishing the `iga → iga` identifier/command/MCP sweep (see Naming & status).

## How it compares

It does **not** try to be a 118-integration desktop app. If you want a self-contained Tauri assistant, projects like [openhuman](https://github.com/tinyhumansai/openhuman) are further along on that path. Iga's bet is different: **Claude Code-native composability** (install/fork/update skill & rule packs), **MemPalace recall quality**, and **contract-guarded native widgets**. Different shape, deliberately.

## License

[MIT](LICENSE). Personal layers (`*.local.md`, `state/`) are yours and never part of the distribution.

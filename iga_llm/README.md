# iga_llm

The single entry point for Iga's headless LLM calls. Engines ask for a **tier**
(`cheap` or `smart`); this package decides which backend and which concrete
model serve it. No engine names a vendor or a model id.

Stdlib only. The `anthropic` backend lazily imports the official SDK, so the
zero-dependency contract holds unless you select it.

## Two capabilities

| Call | What | Backends |
|---|---|---|
| `complete(prompt, tier=...)` | text in, text out | all |
| `run_agent(prompt, tier=..., cwd=...)` | headless agent run with tools (files, MCP, web) | `claude-cli`, `codex-cli` |

API backends have no tools, so `run_agent` raises `LLMError` on them rather
than silently degrading an agent job into a plain completion.

## Configuration (env only)

| Variable | Meaning | Default |
|---|---|---|
| `IGA_PROVIDER` | `claude-cli`, `codex-cli`, `anthropic`, `openai`, `ollama` | `claude-cli` |
| `IGA_MODEL_CHEAP` / `IGA_MODEL_SMART` | model for a tier, overrides the map | see below |
| `IGA_CLAUDE_BIN` / `IGA_CODEX_BIN` | CLI binary path | `claude` / `codex` on PATH |
| `OPENAI_API_KEY` | key for the `openai` backend | required for api.openai.com |
| `IGA_OPENAI_BASE_URL` | any OpenAI-compatible endpoint (LM Studio, OpenRouter, ...) | `https://api.openai.com/v1` |
| `IGA_OLLAMA_BASE_URL` | Ollama endpoint | `http://localhost:11434/v1` |

Anthropic credentials resolve the SDK's way (`ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile).

Default tier map (`core.DEFAULT_MODELS`, the only place model names live):

| Provider | cheap | smart |
|---|---|---|
| `claude-cli` | `sonnet` | `opus` (CLI aliases, so they do not rot) |
| `codex-cli` | codex's own configured default | same |
| `anthropic` | `claude-sonnet-5` | `claude-opus-5` |
| `openai` | `gpt-5-mini` | `gpt-5` |
| `ollama` | none, set `IGA_MODEL_*` | none |

Model precedence: explicit `model=` argument, then `IGA_MODEL_<TIER>`, then the map.

## Use

```python
from iga_llm import complete, run_agent

label = complete("Classify: ...", tier="cheap")
result = run_agent("Research topic X and file a drawer", cwd=iga_home, max_turns=80)
```

From a non-Python caller (the TypeScript email classifier does this):

```bash
echo "Classify: ..." | python3 -m iga_llm --tier cheap
```

```bash
python3 -m iga_llm --resolve --tier smart
```

## Notes

- CLI completions run in a throwaway temp directory, so a pure completion never
  inherits a project's `CLAUDE.md` / `AGENTS.md`.
- `allowed_tools` and `max_turns` are Claude Code concepts. `codex-cli` ignores
  them and gates tools through its sandbox (`workspace-write`) and its own config.
- Secrets are read from the environment inside the call and never appear in an
  error message.

## Tests

```bash
python -m pytest iga_llm/tests -q
```

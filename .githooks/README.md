# iga-guard — LLM privacy/PII guard hooks

pre-commit / commit-msg / pre-push hooks that ask an **LLM** whether your change
is safe to publish to this public, generic OSS repo — blocking anything
user-specific (real people / clients / companies / projects, emails, phones,
finances, secrets, home paths, private URLs, calendar/health/relationship data).
There is **no static denylist** — the judge adapts to whatever a living, personal
setup might contain.

## Enable (any clone)
```
git config core.hooksPath .githooks
```

## Judge backend (auto-detected, first available wins, fail-CLOSED)
- **Claude Code** — `claude` on PATH  (`IGA_GUARD_MODEL`, default `claude-sonnet-4-6`)
- **GitHub Models** — `gh models` extension  (`IGA_GUARD_GH_MODEL`, default `openai/gpt-4o`)

If no judge is available, commits/pushes are **blocked** (a guard you can't run
must not silently pass).

## Overrides (use sparingly)
- `IGA_GUARD_OFF=1 git …` — skip the guard for one command
- `IGA_GUARD_MODEL` / `IGA_GUARD_GH_MODEL` — choose the model
- `IGA_GUARD_MAXBYTES` — max diff bytes sent to the judge (default 120000)

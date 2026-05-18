# iga-mcp — Iga MCP Server

Exposes a persistent [Iga](https://github.com/pmioduszewski/iga-assistant) session
and its skill engines as MCP tools, usable from any Claude Code session.

## Tools

| Tool | Description |
|------|-------------|
| `iga_ask(prompt)` | Send a natural-language prompt to the persistent Iga session. Returns her reply. |
| `iga_status()` | Report session health (turn count, last modified, config). |
| `iga_reset(confirm)` | Archive the current session JSONL; next call starts fresh. |
| `iga_habit_log(habit, op, date, amount)` | Log a habit completion. `op` ∈ `add`/`remove`/`set`. `date` = YYYY-MM-DD or `today`. |
| `iga_habit_summary()` | Return the current habit-tracker digest as structured JSON. |
| `iga_mood_log(emotion, note, at, people, places, events)` | Log a mood/emotional state. |
| `iga_mood_summary(days)` | Return the mood digest for the last N days (default 14). |

The skill tools (`iga_habit_*`, `iga_mood_*`) call the skill engines directly
with the state directory resolved server-side — no SKILL.md reading, no guessing.

## Two-layer model

| Layer | What it is |
|-------|------------|
| **Code (public)** | `iga_mcp/` — the MCP server; generic, no personal data. |
| **Personal config** | `.iga-session-id` + `IGA_*` environment variables — private, never committed. |

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IGA_HOME` | `~/Gaia` | Iga orchestration home directory. Skills live under `$IGA_HOME/skills/`. |
| `IGA_STATE_DIR` | `$IGA_HOME/state` | Substrate state root. Override in tests to a temp dir for isolation. |
| `IGA_SESSION_ID` | read from `$IGA_HOME/.iga-session-id` | UUID of the persistent Iga session. |
| `IGA_CLAUDE_BIN` | `claude` (on PATH) | Path to the `claude` CLI binary. |
| `IGA_TIMEOUT` | `120` | Per-call timeout in seconds for `iga_ask`. |
| `IGA_MCP_STYLE` | `gaia-compact` | Output style name. Set to `""` to inherit session default. |
| `IGA_MCP_MODEL` | *(inherit)* | Model override for MCP calls, e.g. `claude-sonnet-4-6`. |

## Installation

### Quick path (recommended) — one idempotent command

```bash
scripts/setup-iga-mcp.sh
```

Creates the venv, editable-installs the package, registers `iga` with
Claude Code at **user scope**, and **detects VS Code / Cursor** and offers
to add a user-level entry (asks first; merge-only). Re-runnable. Flags:
`--dry-run`, `--yes`, `--venv DIR`. `/iga install iga-mcp` then lets
`/iga status` detect a missing/broken `iga` MCP and re-run this for you.

The manual steps below are the same thing by hand.

### 1. Create a venv and install dependencies

```bash
python3 -m venv ~/.venvs/iga-mcp
~/.venvs/iga-mcp/bin/pip install mcp>=1.0
```

### 2. Register with Claude Code

**Option A — `claude mcp add` (recommended)**

```bash
claude mcp add iga \
  --command ~/.venvs/iga-mcp/bin/python \
  --args "-m" "iga_mcp.server" \
  --env PYTHONPATH=/path/to/iga-assistant/iga_mcp/src \
  --env IGA_HOME=/path/to/iga-assistant \
  --cwd /path/to/iga-assistant
```

Replace `/path/to/iga-assistant` with the absolute path to your clone.

**Option B — `.mcp.json` snippet**

Add to the repo-root `.mcp.json` (edit the `<...>` placeholders):

```json
{
  "mcpServers": {
    "iga": {
      "command": "<home>/.venvs/iga-mcp/bin/python",
      "args": ["-m", "iga_mcp.server"],
      "cwd": "<repo-root>",
      "env": {
        "PYTHONPATH": "<repo-root>/iga_mcp/src",
        "IGA_HOME": "<repo-root>"
      }
    }
  }
}
```

The `.mcp.json` at the repo root already contains the generic template (with
`<repo-root>` and `<home>` placeholders) — edit those two lines and you're done.

### 3. Personal config

```bash
# Create the session ID file (one-time bootstrap):
uuidgen > ~/Gaia/.iga-session-id
```

Optionally set `IGA_STATE_DIR` in your shell profile to point to a non-default
state root (useful in CI or multi-profile setups).

## Running tests

```bash
PYTHONPATH=iga_mcp/src python3 -m unittest discover iga_mcp/tests
```

Tests use a `TemporaryDirectory` as `IGA_STATE_DIR` and assert that
`~/Gaia/state` is never written.

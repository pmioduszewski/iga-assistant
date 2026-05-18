---
name: iga-mcp
description: The Iga MCP server — exposes Iga (and skill-contributed habit/mood tools) to any MCP client (Claude Code, VS Code, Cursor). Generic/public layer; wiring is personal.
status: stable
prerequisites:
  - name: iga-mcp-server
    description: The `iga` MCP server is installed and registered, so Iga (and external agents) can call habit/mood/ask tools instead of slow CLI archaeology.
    check: mcp(iga)
    guide: scripts/setup-iga-mcp.sh
    severity: warning
intent_triggers:
  - iga mcp
  - install mcp
  - setup mcp
  - mcp not connecting
  - wire iga
---

# iga-mcp

The `iga` MCP server is the typed tool surface for Iga: `iga_ask`,
`iga_status`, `iga_reset`, plus skill-contributed tools
(`iga_habit_log`, `iga_habit_summary`, `iga_habit_list`,
`iga_mood_log`, `iga_mood_summary`). Calling these is one fast typed
call instead of reading SKILL.md + shelling to a CLI.

## Two layers (same contract as every other pack)

| Layer | What | Where |
|---|---|---|
| Generic / public | server code + tool surface | `iga_mcp/` (in repo) |
| Personal / local | the venv, the per-client registration, `.iga-session-id`, `IGA_*` env | your machine — never committed |

## Install / wire (one command, idempotent)

```sh
scripts/setup-iga-mcp.sh
```

It creates the venv, editable-installs `iga_mcp`, registers `iga` with
**Claude Code** at user scope, and **detects VS Code / Cursor** and
offers to add a user-level `mcp.json` entry (asks first; merge-only,
never clobbers other servers). Re-runnable any time. Flags:
`--dry-run`, `--yes`, `--venv DIR`. Full manual steps:
[`iga_mcp/README.md`](../iga_mcp/README.md).

## Status detection

This pack declares a `prerequisites:` check (`mcp(iga)`). After
`/iga install iga-mcp`, `/iga status` reports if the `iga` MCP is
missing/disconnected and offers to run the setup guide above. So a
fresh clone has a real "is it wired? → fix it" path, not just prose.

## Clients

- **Claude Code** — user scope (`claude mcp add -s user`), all sessions.
- **VS Code / Cursor** — user-level `mcp.json` (`{"servers":{"iga":…}}`),
  all workspaces. Restart the client / "MCP: Restart" to connect.

---
name: iga
description: Iga admin commands for Codex (status, rules, install, uninstall, check-updates, diff, update, help) plus user-defined daily commands such as gm or eod. Use when the user writes "$iga <command>", "/iga <command>", or "iga <command>".
metadata:
  short-description: Iga admin and daily commands
---

# iga (Codex entry point)

Claude Code exposes these commands as the `/iga` slash command. Codex has no
project slash command files, so this skill is the same entry point: the Codex
desktop app lists it in its `/` menu as `/iga`, and Codex CLI invokes it as
`$iga`. There is ONE source of truth for the command behaviour and it is not this file.

## What to do

1. Take everything the user wrote after `iga` as the arguments (it may be empty).
2. Read `.claude/commands/iga.md` from the repository root, in full.
3. Follow it exactly, treating every `$ARGUMENTS` placeholder as those arguments.

If `.claude/commands/iga.md` is missing, say so and stop. Do not improvise the
commands from memory.

## Codex translations

That file is written for Claude Code. Where it names a Claude-only mechanism,
use the Codex equivalent:

| The file says | In Codex do |
|---|---|
| `claude mcp list` | `codex mcp list` |
| `AskUserQuestion` | ask the question in plain text, offer the 2 to 4 options, wait for the answer |
| check `~/.claude/settings.json` for a `UserPromptSubmit` time hook | check `~/.codex/hooks.json`, `.codex/hooks.json`, and the `[hooks]` table of either `config.toml`, for a `UserPromptSubmit` hook whose command runs `scripts/codex/hooks/time_context.py` (installed by `scripts/setup-iga-mcp.sh`) or starts with `date`. A project-level hook only runs once the user has trusted it via `/hooks`. `config.toml` can hold tokens: look only at its `[hooks]` table and never print other values from it |
| `/model` | tell the user to check the model in their Codex session |
| "restart Claude Code sessions" | restart the Codex session |

Everything else (file paths, the provenance frontmatter, the prereq check DSL,
the confirmation gates before writing or deleting) applies unchanged. The
confirmation gates are not optional in Codex either: never install, update or
uninstall a pack without showing the user what will change and getting a yes.

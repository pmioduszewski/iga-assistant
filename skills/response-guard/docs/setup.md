# Setup

Requires Python 3.11+ and command hooks. From the Iga clone:

```sh
python3 skills/response-guard/engine/install.py --dry-run
python3 skills/response-guard/engine/install.py
```

The installer merges UserPromptSubmit and Stop handlers into this clone's
`.claude/settings.local.json` and `.codex/hooks.json`, preserving other settings.
Use `--host claude` or `--host codex` for one host; `--max-words 90` changes
the cap. Reinstalling replaces only this clone's guard. On Windows use `python`.

Restart Claude Code. In Codex, start a new chat and review the new definitions
with `/hooks`. The installer does not alter hook trust, global configuration,
MCP registration or credentials.

## Behavior and limits

- Explicit requests such as "explain in detail", "step by step" or a longer
  requested word count are exempt. Detection is an English heuristic; unusual
  phrasing can be missed. Accepting the previous answer's detail offer is exempt.
- One Stop rewrite is allowed. Another Stop continuation already in progress
  is left alone. The rewrite can still exceed the cap.
- The original can already have rendered in either host. No MessageDisplay
  filtering is installed. Rewriting adds generation time and usage; the local
  counter needs no separate model call.
- Malformed events, missing state and storage errors fail open. Chats outside
  this clone, disabled hooks, Claude `--bare` and cloud ChatGPT are not covered.
- `state/response-guard/` stores control flags per host/session, not prompts or
  answers. It is separate from MemPalace memory.

Current contracts: [Claude Code](https://code.claude.com/docs/en/hooks) and
[Codex](https://learn.chatgpt.com/docs/hooks).

## Disable or remove

Set `IGA_RESPONSE_GUARD_OFF=1` in the host environment for a temporary bypass.
Remove only this clone's guard handlers with:

```sh
python3 skills/response-guard/engine/install.py --uninstall
```

Existing state remains. Restart the host after removal.

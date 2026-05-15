# Mock MCP server

A minimal stdio MCP stub that serves fixture JSON for a fixed set of tool names. It is **not** wired into the current scenario runner (which uses inline fixtures for determinism and speed). It exists so a future `claude -p --mcp-config` integration can exercise the real Claude Code prompt-assembly path.

## Usage

```bash
MOCK_MCP_FIXTURES='{"gmail.triage":"gmail/triage-clean.json","todoist.find":"todoist/baseline.json"}' \
  pnpm run mock-mcp
```

Then point a Claude Code session at it with an `--mcp-config` snippet like:

```json
{
  "mcpServers": {
    "iga-evals-mock": {
      "command": "tsx",
      "args": ["mocks/mcp-server.ts"],
      "env": {
        "MOCK_MCP_FIXTURES": "{\"gmail.triage\":\"gmail/triage-clean.json\"}"
      }
    }
  }
}
```

## Smoke test

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | \
  MOCK_MCP_FIXTURES='{}' pnpm run mock-mcp
```

Should print a JSON-RPC reply describing the server.

## What it does NOT implement

- Resources, prompts, sampling — only `tools/list` and `tools/call`.
- Streaming responses.
- Auth, capability negotiation beyond `protocolVersion`.

For the current eval loop, see `src/runScenario.ts` — it loads the same fixtures inline and skips the IPC entirely.

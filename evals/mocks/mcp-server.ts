#!/usr/bin/env tsx
/**
 * Minimal stdio MCP server stub. Serves fixture JSON for a small set of tool
 * names. Intentionally NOT a full MCP implementation — just enough that a
 * `claude -p --mcp-config` invocation can call the stubbed tools and get
 * deterministic fixture data back.
 *
 * The current scenario runner uses inline fixtures (see src/runScenario.ts)
 * and does NOT spawn this server. We ship it for future work where we want
 * to exercise the real Claude Code prompt-assembly path end-to-end.
 *
 * Protocol shape (simplified):
 *   - Reads newline-delimited JSON-RPC requests on stdin.
 *   - Supports `initialize`, `tools/list`, `tools/call`.
 *   - For `tools/call`, looks up the requested tool name in a static map
 *     (env-configured) and returns the JSON fixture verbatim.
 *
 * See mocks/README.md for the env-var contract and an end-to-end smoke test.
 */
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

const __dirname = dirname(fileURLToPath(import.meta.url));

interface FixtureMap {
  [toolName: string]: string; // path relative to fixtures/mcp-responses/
}

// Configure via MOCK_MCP_FIXTURES env var: JSON object mapping tool name -> path
// Example: MOCK_MCP_FIXTURES='{"gmail.triage":"gmail/triage-clean.json"}'
const fixtureMap: FixtureMap = JSON.parse(process.env.MOCK_MCP_FIXTURES || "{}");

function loadFixture(path: string): unknown {
  const abs = resolve(__dirname, "..", "fixtures", "mcp-responses", path);
  return JSON.parse(readFileSync(abs, "utf8"));
}

function reply(id: number | string, result: unknown) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function replyError(id: number | string | null, code: number, message: string) {
  process.stdout.write(
    JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n"
  );
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let msg: any;
  try {
    msg = JSON.parse(line);
  } catch {
    replyError(null, -32700, "Parse error");
    return;
  }
  const { id, method, params } = msg;

  switch (method) {
    case "initialize":
      reply(id, {
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "iga-evals-mock-mcp", version: "0.1.0" },
      });
      return;

    case "tools/list":
      reply(id, {
        tools: Object.keys(fixtureMap).map((name) => ({
          name,
          description: `Mock fixture-backed tool: ${name}`,
          inputSchema: { type: "object", properties: {}, additionalProperties: true },
        })),
      });
      return;

    case "tools/call": {
      const toolName = params?.name as string | undefined;
      if (!toolName || !(toolName in fixtureMap)) {
        replyError(id, -32602, `Unknown or unmapped tool: ${toolName}`);
        return;
      }
      try {
        const data = loadFixture(fixtureMap[toolName]);
        reply(id, {
          content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
          isError: false,
        });
      } catch (err) {
        replyError(id, -32603, `Fixture load failed: ${(err as Error).message}`);
      }
      return;
    }

    default:
      replyError(id, -32601, `Method not found: ${method}`);
  }
});

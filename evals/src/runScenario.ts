import Anthropic from "@anthropic-ai/sdk";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { parse as parseYaml } from "yaml";
import type { IgaRunResult, Scenario } from "./types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

const DEFAULT_SUT_MODEL = process.env.IGA_SUT_MODEL || "claude-sonnet-4-6";

/**
 * Load a scenario YAML file. Resolves drawers/MCP fixtures eagerly so the runner
 * can sanity-check before spending tokens.
 */
export async function loadScenario(scenarioPath: string): Promise<{
  scenario: Scenario;
  drawers: unknown[];
  mcpFixtures: Record<string, unknown>;
}> {
  const raw = await readFile(scenarioPath, "utf8");
  const scenario = parseYaml(raw) as Scenario;

  const drawers: unknown[] = [];
  for (const file of scenario.drawers) {
    const path = resolve(ROOT, "fixtures", "drawers", file);
    drawers.push(JSON.parse(await readFile(path, "utf8")));
  }

  const mcpFixtures: Record<string, unknown> = {};
  for (const [tool, file] of Object.entries(scenario.mcp_fixtures)) {
    const path = resolve(ROOT, "fixtures", "mcp-responses", file);
    mcpFixtures[tool] = JSON.parse(await readFile(path, "utf8"));
  }

  return { scenario, drawers, mcpFixtures };
}

/**
 * Run Iga-under-test for a scenario.
 *
 * IMPLEMENTATION CHOICE: we call the Anthropic SDK directly with a stripped-down
 * Iga system prompt + the relevant tool definitions, providing fixture-backed
 * tool results inline. This is more deterministic than shelling out to `claude -p`
 * with --mcp-config, and avoids the brittleness of the full Claude Code harness
 * for the eval inner loop. See README.md "Why not claude -p?".
 *
 * The downside: we're not exercising the actual Claude Code prompt assembly.
 * That's fine for the regression we care about (stale-fact surfacing) — that
 * failure lives in the model's reasoning over retrieved context, which we
 * faithfully reproduce here.
 */
export async function runIgaUnderTest(args: {
  scenario: Scenario;
  drawers: unknown[];
  mcpFixtures: Record<string, unknown>;
  client?: Anthropic;
  model?: string;
}): Promise<IgaRunResult> {
  const client = args.client ?? new Anthropic();
  const model = args.model ?? DEFAULT_SUT_MODEL;

  // Compact Iga system prompt — keeps the eval cheap. Captures the relevant
  // behavior: TL;DR style, MemPalace-grounded, surface only actionable items.
  const system = [
    "You are Iga, Alex Rivera's personal AI assistant.",
    "Style: TL;DR first, ~150 words max, bullet lists preferred. No filler.",
    "Memory: facts in <memory> are your MemPalace. Treat drawers marked OBSOLETE / superseded as no-longer-true.",
    "Mail/Tasks/Calendar in <tools_state> are today's tool snapshots.",
    "Your job for /gm: produce a morning briefing. Surface only items that are STILL TRUE and STILL ACTIONABLE given memory.",
  ].join("\n");

  const memoryBlock = `<memory>\n${JSON.stringify(args.drawers, null, 2)}\n</memory>`;
  const toolsBlock = `<tools_state>\n${JSON.stringify(args.mcpFixtures, null, 2)}\n</tools_state>`;

  const response = await client.messages.create({
    model,
    max_tokens: 1500,
    system,
    messages: [
      {
        role: "user",
        content: `${memoryBlock}\n\n${toolsBlock}\n\nUser: ${args.scenario.user_message}`,
      },
    ],
  });

  const assistant_message = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("\n");

  return {
    scenario_id: args.scenario.id,
    sut_model: model,
    assistant_message,
    // Inline-fixture mode — no real tool calls. We document the fixtures the
    // model was given so the judge transcript stays informative.
    tool_calls: Object.entries(args.mcpFixtures).map(([name, fx]) => ({
      name,
      input: { fixture: true },
      output_summary: summarize(fx),
    })),
  };
}

function summarize(fx: unknown): string {
  const s = JSON.stringify(fx);
  return s.length > 400 ? s.slice(0, 400) + "..." : s;
}

/**
 * Format an Iga run as a transcript suitable for the judge prompt.
 */
export function formatTranscript(scenario: Scenario, result: IgaRunResult): string {
  return [
    `# Scenario: ${scenario.id}`,
    `# Description: ${scenario.description}`,
    ``,
    `## User message`,
    scenario.user_message,
    ``,
    `## Tool fixtures provided to Iga`,
    ...result.tool_calls.map((c) => `- ${c.name}: ${c.output_summary}`),
    ``,
    `## Iga's response`,
    result.assistant_message,
  ].join("\n");
}

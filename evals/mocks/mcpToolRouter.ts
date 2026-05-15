/**
 * Tier-2 mock MCP "server".
 *
 * NOT a real stdio MCP server — we'd never spawn one inside vitest. Instead,
 * this module implements the Anthropic SDK tool-use loop: we declare a tool
 * catalog, intercept `tool_use` blocks in the assistant's response, look up
 * canned fixture data, and feed back `tool_result` content blocks until the
 * model produces a final assistant text turn.
 *
 * This is what makes tier 2 different from tier 1: the model itself decides
 * whether (and how) to call `mempalace_search` before classifying the inbox.
 * Tier 1 pre-injects all retrieved context inline, which is exactly the
 * failure mode (skipped-search) we want to be able to catch.
 */
import Anthropic from "@anthropic-ai/sdk";
import type { FixtureFile } from "./fixtureLoader.js";
import { pickFixture } from "./fixtureLoader.js";

/** A single recorded tool call (what Iga asked for, what we returned). */
export interface RecordedToolCall {
  /** Monotonic index — useful for ordering assertions. */
  index: number;
  tool_name: string;
  input: unknown;
  /** Fixture id used, or `null` if we synthesized an empty result. */
  fixture_id: string | null;
  /** Stringified summary of what Iga got back. Keeps transcripts readable. */
  output_summary: string;
}

export interface ToolRouterResult {
  /** Final assistant text turn (last text content blocks concatenated). */
  assistant_message: string;
  /** All tool calls Iga made, in call order. */
  tool_calls: RecordedToolCall[];
  /** Number of conversation turns (assistant messages produced). */
  turns: number;
  /** Stop reason from the final assistant turn. */
  stop_reason: string | null;
}

/**
 * Tool definitions Iga sees. The schemas are loose by design — we want Iga
 * to drive the call shape, not the harness. Mirrors how the real MCP tools
 * look at the SDK layer.
 */
export const TIER2_TOOLS: Anthropic.Tool[] = [
  {
    name: "mempalace_search",
    description:
      "Search MemPalace (the assistant's persistent semantic memory). Use this whenever a user message mentions a person, project, company, domain, decision, or any entity that might already be in memory. Returns matching drawers (memory cards) with title, content, wing, and superseded_by metadata. ALWAYS prefer searching before judging whether something is actionable.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Natural-language search query." },
        wing: {
          type: "string",
          description: "Optional wing filter (people | decisions | projects | sessions | preferences | rules).",
        },
        limit: { type: "number" },
      },
      required: ["query"],
    },
  },
  {
    name: "manage_email",
    description:
      "Gmail tool. Use action=list to fetch recent triaged messages, action=read to fetch a message body, action=label to apply labels. Returns deterministic snapshots in this eval.",
    input_schema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["list", "read", "label"] },
        query: { type: "string" },
        id: { type: "string" },
      },
      required: ["action"],
    },
  },
  {
    name: "find-tasks",
    description: "Todoist tool. Lists or searches the user's tasks.",
    input_schema: {
      type: "object",
      properties: {
        filter: { type: "string", description: "e.g. 'today', 'overdue', a project name" },
      },
    },
  },
  {
    name: "list_events",
    description: "Google Calendar tool. Lists events in a window.",
    input_schema: {
      type: "object",
      properties: {
        start: { type: "string", description: "ISO date" },
        end: { type: "string", description: "ISO date" },
      },
    },
  },
];

const MAX_TOOL_TURNS = 8; // safety cap — Iga loops at most this many tool rounds

/**
 * Drive a tool-use conversation with Iga. Continues calling the model and
 * answering tool_use blocks with fixture-backed tool_result blocks until the
 * model produces a final assistant message (stop_reason !== "tool_use") or
 * we hit MAX_TOOL_TURNS.
 */
export async function runToolLoop(args: {
  client: Anthropic;
  model: string;
  system: string;
  userMessage: string;
  fixtures: FixtureFile[];
  /** If true, log each tool call to console for debugging. */
  trace?: boolean;
}): Promise<ToolRouterResult> {
  const messages: Anthropic.MessageParam[] = [
    { role: "user", content: args.userMessage },
  ];

  const recorded: RecordedToolCall[] = [];
  let turn = 0;
  let stop_reason: string | null = null;
  let lastTextBlocks: Anthropic.TextBlock[] = [];

  while (turn < MAX_TOOL_TURNS) {
    const response: Anthropic.Message = await args.client.messages.create({
      model: args.model,
      max_tokens: 1500,
      system: args.system,
      tools: TIER2_TOOLS,
      messages,
    });

    turn += 1;
    stop_reason = response.stop_reason ?? null;
    lastTextBlocks = response.content.filter(
      (b): b is Anthropic.TextBlock => b.type === "text",
    );

    const toolUses = response.content.filter(
      (b): b is Anthropic.ToolUseBlock => b.type === "tool_use",
    );

    // Append the assistant turn to the message history regardless.
    messages.push({ role: "assistant", content: response.content });

    if (response.stop_reason !== "tool_use" || toolUses.length === 0) {
      break; // final answer
    }

    // Resolve each tool_use into a tool_result block.
    const toolResults: Anthropic.ToolResultBlockParam[] = [];
    for (const tu of toolUses) {
      const fixture = pickFixture(args.fixtures, tu.name, tu.input);
      const payload = fixture?.response ?? noResultsEnvelope(tu.name);
      const text = JSON.stringify(payload, null, 2);

      const summary = text.length > 300 ? text.slice(0, 300) + "..." : text;
      recorded.push({
        index: recorded.length,
        tool_name: tu.name,
        input: tu.input,
        fixture_id: fixture?.id ?? null,
        output_summary: summary,
      });
      if (args.trace) {
        // eslint-disable-next-line no-console
        console.log(
          `[tool] ${tu.name}(${JSON.stringify(tu.input)}) -> ${fixture?.id ?? "no-fixture"}`,
        );
      }

      toolResults.push({
        type: "tool_result",
        tool_use_id: tu.id,
        content: text,
      });
    }

    messages.push({ role: "user", content: toolResults });
  }

  const assistant_message = lastTextBlocks.map((b) => b.text).join("\n");

  return {
    assistant_message,
    tool_calls: recorded,
    turns: turn,
    stop_reason,
  };
}

/**
 * What Iga sees when no fixture matches her call. Shaped per-tool to avoid
 * making her think the tool is broken (which would derail reasoning).
 */
function noResultsEnvelope(toolName: string): unknown {
  switch (toolName) {
    case "mempalace_search":
      return { results: [], note: "No drawers matched." };
    case "manage_email":
      return { messages: [] };
    case "find-tasks":
      return { tasks: [] };
    case "list_events":
      return { events: [] };
    default:
      return { ok: true, data: null };
  }
}

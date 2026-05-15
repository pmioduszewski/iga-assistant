/**
 * Tier-2 scenario runner.
 *
 * Where tier 1 (`runScenario.ts`) inlines drawers + tool snapshots inside
 * `<memory>` / `<tools_state>` blocks, tier 2 hands Iga *real* tool schemas
 * and lets her decide which to call. The mock router (mocks/mcpToolRouter.ts)
 * answers each tool_use with fixture data, deterministically.
 *
 * This is what makes tier 2 the right place to catch the "skipped the search
 * step entirely" failure mode — tier 1 cannot, because it pre-injects.
 */
import Anthropic from "@anthropic-ai/sdk";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { parse as parseYaml } from "yaml";
import {
  runToolLoop,
  type RecordedToolCall,
  type ToolRouterResult,
} from "../mocks/mcpToolRouter.js";
import { loadFixtureFile, type FixtureFile } from "../mocks/fixtureLoader.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

const DEFAULT_SUT_MODEL = process.env.IGA_SUT_MODEL || "claude-sonnet-4-6";

/**
 * Tier-2 scenario shape. Mirrors tier 1 where possible; adds the assertion
 * vocabulary that's the whole point of the tier (tool-call sequencing).
 */
export interface Tier2Scenario {
  id: string;
  description: string;
  expected_status?: string;
  /** Fixture file paths (relative to fixtures/mcp-responses/). */
  fixtures: string[];
  /** Iga's user-facing prompt. */
  user_message: string;
  /** Optional: prepend a system-prompt override (else use the default). */
  system_override?: string;
  /** Judge selection (reuses tier-1 judges). */
  judge: "relevance" | "staleness";
  judge_criteria: string;
  assertions: Tier2Assertions;
}

export interface Tier2Assertions {
  /** Tool names that MUST be called at least once. */
  must_call?: string[];
  /** Tool names that MUST NOT be called. */
  must_not_call?: string[];
  /**
   * Each entry: a tool name + a list of substrings; the test passes if at
   * least one call to that tool had args (JSON-stringified, case-insensitive)
   * containing any of the substrings.
   */
  must_call_with_args_matching?: Array<{
    tool: string;
    /** Match passes if ANY substring is present in the args of ANY matching call. */
    any_of: string[];
    /** Optional: human-friendly id surfaced in failure messages. */
    label?: string;
  }>;
  /**
   * Ordering constraints. The key tool's FIRST call must occur before the
   * value tool's FIRST call. Special value `"final_answer"` means "before the
   * final assistant text turn".
   */
  must_call_before?: Record<string, string>;
}

export interface Tier2RunResult extends ToolRouterResult {
  scenario_id: string;
  sut_model: string;
}

const TIER2_SCENARIOS_DIR = resolve(ROOT, "scenarios-tier2");

export async function loadTier2Scenario(scenarioPath: string): Promise<{
  scenario: Tier2Scenario;
  fixtures: FixtureFile[];
}> {
  const raw = await readFile(scenarioPath, "utf8");
  const scenario = parseYaml(raw) as Tier2Scenario;

  const fixtures: FixtureFile[] = [];
  for (const file of scenario.fixtures) {
    fixtures.push(await loadFixtureFile(file));
  }
  return { scenario, fixtures };
}

/** Default Iga system prompt used for tier 2 unless the scenario overrides. */
const DEFAULT_SYSTEM = [
  "You are Iga, Alex Rivera's personal AI assistant.",
  "Style: TL;DR first, ~150 words max, bullet lists preferred. No filler.",
  "",
  "You have tools. Memory lives in MemPalace (call `mempalace_search`).",
  "Mail / Tasks / Calendar are read via their respective tools.",
  "",
  "BINDING RULES:",
  "- Before classifying any inbox item, task, or external mention of a company/person/project as ACTIONABLE, search MemPalace for the relevant entities. Skipping this step is the canonical failure.",
  "- Treat drawers marked OBSOLETE / superseded_by as NO LONGER TRUE.",
  "- If memory contradicts an inbox item, do NOT surface it as an action — explain briefly that it's stale.",
  "- For trivial requests where nothing in MemPalace is plausibly relevant (e.g. summarizing a grocery list), don't waste a search call.",
].join("\n");

export async function runIgaUnderTestTier2(args: {
  scenario: Tier2Scenario;
  fixtures: FixtureFile[];
  client?: Anthropic;
  model?: string;
  trace?: boolean;
}): Promise<Tier2RunResult> {
  const client = args.client ?? new Anthropic();
  const model = args.model ?? DEFAULT_SUT_MODEL;

  const system = args.scenario.system_override ?? DEFAULT_SYSTEM;

  const loop = await runToolLoop({
    client,
    model,
    system,
    userMessage: args.scenario.user_message,
    fixtures: args.fixtures,
    trace: args.trace,
  });

  return {
    ...loop,
    scenario_id: args.scenario.id,
    sut_model: model,
  };
}

/**
 * Apply assertions to a tier-2 run result. Returns the list of violations
 * (empty array = pass). The test layer turns these into vitest failures;
 * keeping it pure here makes it easy to unit-test.
 */
export function checkAssertions(
  assertions: Tier2Assertions,
  calls: RecordedToolCall[],
): string[] {
  const violations: string[] = [];

  const calledNames = new Set(calls.map((c) => c.tool_name));

  for (const t of assertions.must_call ?? []) {
    if (!calledNames.has(t)) violations.push(`must_call: ${t} was never called`);
  }
  for (const t of assertions.must_not_call ?? []) {
    if (calledNames.has(t)) violations.push(`must_not_call: ${t} was called (forbidden)`);
  }

  for (const m of assertions.must_call_with_args_matching ?? []) {
    const matching = calls.filter((c) => c.tool_name === m.tool);
    if (matching.length === 0) {
      violations.push(
        `must_call_with_args_matching: ${m.tool} was never called (label=${m.label ?? "n/a"})`,
      );
      continue;
    }
    const haystack = JSON.stringify(matching.map((c) => c.input)).toLowerCase();
    const hit = m.any_of.some((needle) => haystack.includes(needle.toLowerCase()));
    if (!hit) {
      violations.push(
        `must_call_with_args_matching: ${m.tool} called but args did not contain any of [${m.any_of.join(", ")}] (label=${m.label ?? "n/a"})`,
      );
    }
  }

  for (const [before, after] of Object.entries(assertions.must_call_before ?? {})) {
    const firstBefore = calls.find((c) => c.tool_name === before)?.index;
    if (firstBefore === undefined) {
      violations.push(`must_call_before: ${before} was never called`);
      continue;
    }
    if (after === "final_answer") {
      // By construction, any recorded tool call is before the final answer
      // (we stop recording once stop_reason !== "tool_use"). Pass.
      continue;
    }
    const firstAfter = calls.find((c) => c.tool_name === after)?.index;
    if (firstAfter === undefined) continue; // can't violate if never called
    if (firstBefore >= firstAfter) {
      violations.push(
        `must_call_before: ${before} (idx ${firstBefore}) was not before ${after} (idx ${firstAfter})`,
      );
    }
  }

  return violations;
}

/** Format a tier-2 run as a judge-ready transcript. */
export function formatTier2Transcript(
  scenario: Tier2Scenario,
  result: Tier2RunResult,
): string {
  const callsBlock = result.tool_calls.length
    ? result.tool_calls
        .map(
          (c) =>
            `${c.index}. ${c.tool_name}(${JSON.stringify(c.input)}) -> ${c.output_summary}`,
        )
        .join("\n")
    : "(no tool calls)";

  return [
    `# Tier-2 scenario: ${scenario.id}`,
    `# Description: ${scenario.description}`,
    ``,
    `## User message`,
    scenario.user_message,
    ``,
    `## Tool calls Iga made (in order)`,
    callsBlock,
    ``,
    `## Iga's final response`,
    result.assistant_message,
  ].join("\n");
}

export { TIER2_SCENARIOS_DIR };

/**
 * Shared types for the Iga eval harness.
 */

export type JudgeOutcome = "pass" | "fail";

/**
 * Output of a single judge call. Critique-then-binary, per Hamel's Honeycomb pattern.
 * See: https://hamel.dev/blog/posts/llm-judge/
 */
export interface JudgeResult {
  critique: string;
  outcome: JudgeOutcome;
  violations: string[];
  /** Raw model output, kept for debugging / alignment work. */
  raw: string;
}

/**
 * Loaded scenario definition. Mirrors the on-disk YAML.
 */
export interface Scenario {
  id: string;
  description: string;
  /** Optional human-readable status for currently-failing regression cases. */
  expected_status?: string;
  /** Drawer fixture file names (relative to fixtures/drawers/) used as MemPalace state. */
  drawers: string[];
  /** Per-tool MCP fixture file mapping. Tool name => path relative to fixtures/mcp-responses/. */
  mcp_fixtures: Record<string, string>;
  /** Iga's user-facing prompt for this scenario (e.g. "/gm"). */
  user_message: string;
  /** Which judge to use. */
  judge: "relevance" | "staleness";
  /**
   * What the judge should specifically watch for in this scenario.
   * Passed into the judge prompt as the "criteria" block.
   */
  judge_criteria: string;
}

/**
 * Output of a single scenario execution before judging.
 */
export interface IgaRunResult {
  scenario_id: string;
  sut_model: string;
  assistant_message: string;
  tool_calls: Array<{ name: string; input: unknown; output_summary: string }>;
}

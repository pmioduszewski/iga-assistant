/**
 * Tier-2 scenario tests.
 *
 * Differs from tier 1 in one important way: instead of inlining drawer +
 * tool snapshots in the prompt, we hand Iga real tool schemas and let her
 * DECIDE whether/when to call them. The mock router answers each tool_use
 * with deterministic fixture data.
 *
 * Each scenario asserts on TWO axes:
 *   1. Tool-call behavior (must_call / must_not_call / ordering) — caught
 *      structurally by checkAssertions(). This is the unique tier-2 axis.
 *   2. Final assistant message — judged by the same Opus judge as tier 1.
 *
 * `expected_status: currently_failing ...` switches to `it.fails` exactly
 * like tier 1.
 */
import { describe, it, expect } from "vitest";
import { readdir } from "node:fs/promises";
import { resolve } from "node:path";
import {
  loadTier2Scenario,
  runIgaUnderTestTier2,
  checkAssertions,
  formatTier2Transcript,
  TIER2_SCENARIOS_DIR,
} from "../src/runScenarioTier2.js";
import { runJudge } from "../src/runJudge.js";

const hasApiKey = !!process.env.ANTHROPIC_API_KEY;

describe.skipIf(!hasApiKey)("tier-2 scenarios (live API + tool loop)", async () => {
  const files = (await readdir(TIER2_SCENARIOS_DIR)).filter((f) =>
    f.endsWith(".yaml"),
  );

  for (const file of files) {
    const path = resolve(TIER2_SCENARIOS_DIR, file);
    const { scenario, fixtures } = await loadTier2Scenario(path);

    const isCurrentlyFailing =
      scenario.expected_status?.startsWith("currently_failing");
    const runner = isCurrentlyFailing ? it.fails : it;

    runner(`${scenario.id}: tool assertions + judge pass`, async () => {
      const run = await runIgaUnderTestTier2({ scenario, fixtures });

      // Structural assertions first — they're cheap and isolate the failure
      // mode (skipped search vs. bad reasoning over search results).
      const violations = checkAssertions(scenario.assertions, run.tool_calls);

      // Then the judge — verifies the surface-level output is sane.
      const transcript = formatTier2Transcript(scenario, run);
      const judgeResult = await runJudge({
        judgeName: scenario.judge,
        transcript,
        criteria: scenario.judge_criteria,
      });

      if (violations.length || judgeResult.outcome !== "pass") {
        // eslint-disable-next-line no-console
        console.error(
          [
            `[${scenario.id}] FAIL`,
            `  tool_calls:`,
            ...run.tool_calls.map(
              (c) => `    - ${c.tool_name}(${JSON.stringify(c.input)})`,
            ),
            violations.length
              ? `  assertion violations:\n    - ${violations.join("\n    - ")}`
              : "",
            judgeResult.outcome !== "pass"
              ? `  judge violations:\n    - ${judgeResult.violations.join("\n    - ")}\n  judge critique:\n${judgeResult.critique}`
              : "",
          ]
            .filter(Boolean)
            .join("\n"),
        );
      }

      expect(violations, "tool-call assertion violations").toEqual([]);
      expect(judgeResult.outcome).toBe("pass");
    });
  }
});

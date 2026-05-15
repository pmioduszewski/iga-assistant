/**
 * End-to-end scenario tests. For each YAML file in scenarios/, we:
 *   1. Load the scenario + drawer + MCP fixtures.
 *   2. Run Iga-under-test (Sonnet) with the fixtures inlined into context.
 *   3. Send the transcript to the judge (Opus).
 *   4. Assert the judge's outcome matches what the scenario expects.
 *
 * For scenarios marked `expected_status: currently_failing ...`, we use
 * `it.fails` so a green run on a known-broken case becomes a noisy regression
 * signal rather than a false sense of success.
 */
import { describe, it, expect } from "vitest";
import { readdir } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  loadScenario,
  runIgaUnderTest,
  formatTranscript,
} from "../src/runScenario.js";
import { runJudge } from "../src/runJudge.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCENARIOS_DIR = resolve(__dirname, "..", "scenarios");

const hasApiKey = !!process.env.ANTHROPIC_API_KEY;

describe.skipIf(!hasApiKey)("scenarios (live API)", async () => {
  const files = (await readdir(SCENARIOS_DIR)).filter((f) => f.endsWith(".yaml"));

  for (const file of files) {
    const path = resolve(SCENARIOS_DIR, file);
    const { scenario, drawers, mcpFixtures } = await loadScenario(path);

    const isCurrentlyFailing = scenario.expected_status?.startsWith("currently_failing");

    const runner = isCurrentlyFailing ? it.fails : it;
    runner(`${scenario.id}: judge returns pass`, async () => {
      const run = await runIgaUnderTest({ scenario, drawers, mcpFixtures });
      const transcript = formatTranscript(scenario, run);
      const result = await runJudge({
        judgeName: scenario.judge,
        transcript,
        criteria: scenario.judge_criteria,
      });

      // Surface critique on failure for debugging.
      if (result.outcome !== "pass") {
        // eslint-disable-next-line no-console
        console.error(
          `[${scenario.id}] FAIL\n  violations:\n    - ${result.violations.join(
            "\n    - "
          )}\n  critique:\n${result.critique}`
        );
      }
      expect(result.outcome).toBe("pass");
    });
  }
});

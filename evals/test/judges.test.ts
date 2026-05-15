/**
 * Judge unit tests. Two kinds:
 *
 *   1. Parser tests — pure, no network. Lock down the <critique>/<violations>/
 *      <outcome> parsing contract so a future judge-prompt tweak can't silently
 *      break extraction.
 *
 *   2. Calibration tests — live judge calls against hand-labeled ground-truth
 *      transcripts. These are the basis for the >=90% agreement target on a
 *      held-out set, per Hamel's LLM-as-judge alignment loop. The hand-labeled
 *      fixtures are stubbed below; the user replaces them after he runs the first
 *      20 traces (see README "Judge calibration loop").
 */
import { describe, expect, it } from "vitest";
import { parseJudgeOutput } from "../src/runJudge.js";

describe("parseJudgeOutput", () => {
  it("extracts critique, violations, and pass outcome", () => {
    const raw = `
<critique>
Iga correctly suppressed the stale renewal item.
</critique>

<violations>
</violations>

<outcome>pass</outcome>
`;
    const out = parseJudgeOutput(raw);
    expect(out.outcome).toBe("pass");
    expect(out.violations).toEqual([]);
    expect(out.critique).toContain("suppressed");
  });

  it("extracts multi-line violations and fail outcome", () => {
    const raw = `
<critique>Surfaced stale item.</critique>
<violations>
- listed "renew pipevine.example" as a TODO
- referred to the company as "Pipevine"
</violations>
<outcome>fail</outcome>
`;
    const out = parseJudgeOutput(raw);
    expect(out.outcome).toBe("fail");
    expect(out.violations).toHaveLength(2);
    expect(out.violations[0]).toContain("renew pipevine.example");
  });

  it("defaults to fail when no outcome tag is found", () => {
    const out = parseJudgeOutput("just some prose, no tags");
    expect(out.outcome).toBe("fail");
  });

  it("is case-insensitive on tag names", () => {
    const raw = `<CRITIQUE>ok</CRITIQUE><OUTCOME>PASS</OUTCOME>`;
    const out = parseJudgeOutput(raw);
    expect(out.outcome).toBe("pass");
  });
});

/**
 * Calibration ground-truth.
 *
 * The user does NOT hand-label offline (batch annotation isn't a workflow that
 * fits them). Instead, ground truth is built up incrementally by Iga driving the
 * loop from chat: she runs scenarios, self-labels with confidence, and surfaces
 * contested cases via AskUserQuestion one at a time. The user's verdicts are
 * appended to `evals/calibration/contested-cases.jsonl`. See:
 *   - README "Calibration (the Iga-driven loop)"
 *   - src/calibrateInChat.ts (stub — wired once supersedence ships)
 *
 * Once that file has enough entries, load them here and the held-out test
 * below auto-activates. Hand-labeling remains a fallback for contributors
 * willing to do batch labeling but is not the canonical path.
 */
const CALIBRATION_GROUND_TRUTH: Array<{
  name: string;
  judge: "relevance" | "staleness";
  criteria: string;
  transcript: string;
  expected: "pass" | "fail";
}> = [
  // TODO: load entries from evals/calibration/contested-cases.jsonl once
  // Iga has driven the chat-based calibration loop enough times to populate
  // it. See src/calibrateInChat.ts.
];

describe.skipIf(CALIBRATION_GROUND_TRUTH.length === 0)("judge calibration", () => {
  it("agrees with human labels on >=90% of held-out traces", async () => {
    // Implementation deferred until ground truth exists. Pattern:
    //   - Split CALIBRATION_GROUND_TRUTH 50/50 into train/holdout.
    //   - For each holdout item: call runJudge(...) and compare outcome.
    //   - Assert agreement >= 0.9. If <0.9, the judge prompt needs work.
    expect(CALIBRATION_GROUND_TRUTH.length).toBeGreaterThan(0);
  });
});

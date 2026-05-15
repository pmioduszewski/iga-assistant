/**
 * calibrateInChat — Iga-driven, chat-based judge calibration loop.
 *
 * Why this exists
 * ---------------
 * The primary user cannot reliably do offline batch annotation of 20
 * transcripts. The standard Hamel error-analysis loop assumes a human will
 * sit down and hand-label a calibration set; that workflow is dead on arrival
 * here.
 *
 * Instead, Iga drives the loop from chat:
 *
 *   1. Load N scenarios from `evals/scenarios/*.yaml`.
 *   2. Run each one — SUT (Sonnet) generates a transcript, judge (Opus)
 *      emits a verdict + critique.
 *   3. For each transcript, Iga ALSO self-labels with her own judgement and
 *      a confidence score in [0, 1].
 *   4. Mark a case "contested" when:
 *        - Iga's confidence < 0.85, OR
 *        - Iga's self-label disagrees with the judge's verdict.
 *   5. Emit a JSON manifest of contested cases. An Iga session reads this
 *      manifest and walks the user through each contested case ONE AT A TIME
 *      via the AskUserQuestion tool (pass / fail / skip-ambiguous).
 *   6. The user's verdicts get appended to
 *      `evals/calibration/contested-cases.jsonl` as the canonical
 *      ground-truth set.
 *   7. Iterate the judge prompts in `judges/*.md` until Iga's self-labels
 *      agree with the user's verdicts on a held-out subset at ≥90%.
 *
 * Status
 * ------
 * STUB. The actual chat-driving loop is wired once supersedence ships and Iga
 * has the tool surface to call AskUserQuestion from inside a long-running
 * eval session. Function signatures + flow are sketched below; the bodies are
 * intentionally minimal so reviewers see the shape without committing to
 * details that depend on unfinished infrastructure.
 */
import { readdir, appendFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const SCENARIOS_DIR = resolve(ROOT, "scenarios");
const CALIBRATION_DIR = resolve(ROOT, "calibration");
const CONTESTED_PATH = resolve(CALIBRATION_DIR, "contested-cases.jsonl");
const MANIFEST_PATH = resolve(CALIBRATION_DIR, "contested-manifest.json");

const CONFIDENCE_THRESHOLD = 0.85;

export interface ContestedCase {
  scenario_id: string;
  transcript: string;
  judge_verdict: "pass" | "fail";
  judge_critique: string;
  iga_self_label: "pass" | "fail";
  iga_confidence: number; // 0..1
  reason_contested: "low_confidence" | "disagreement_with_judge";
}

export interface UserVerdict {
  scenario_id: string;
  verdict: "pass" | "fail" | "skip";
  note?: string;
  decided_at: string; // ISO timestamp
}

/**
 * Step 1+2+3+4: run scenarios, judge them, self-label, surface contested ones.
 * Returns the manifest of contested cases for the chat layer to walk through.
 */
export async function buildContestedManifest(opts: {
  limit?: number;
}): Promise<ContestedCase[]> {
  // TODO: implement once supersedence-aware retrieval exists so the SUT can
  // be exercised meaningfully. Sketch:
  //
  // const files = (await readdir(SCENARIOS_DIR)).filter((f) =>
  //   f.endsWith(".yaml")
  // );
  // const contested: ContestedCase[] = [];
  // for (const f of files.slice(0, opts.limit ?? files.length)) {
  //   const { scenario, drawers, mcpFixtures } = await loadScenario(...);
  //   const run = await runIgaUnderTest({ scenario, drawers, mcpFixtures });
  //   const transcript = formatTranscript(scenario, run);
  //   const judge = await runJudge({ ... });
  //   const self = await selfLabel({ transcript, criteria: scenario.judge_criteria });
  //   const isContested =
  //     self.confidence < CONFIDENCE_THRESHOLD ||
  //     self.label !== judge.outcome;
  //   if (isContested) {
  //     contested.push({
  //       scenario_id: scenario.id,
  //       transcript,
  //       judge_verdict: judge.outcome,
  //       judge_critique: judge.critique,
  //       iga_self_label: self.label,
  //       iga_confidence: self.confidence,
  //       reason_contested:
  //         self.label !== judge.outcome
  //           ? "disagreement_with_judge"
  //           : "low_confidence",
  //     });
  //   }
  // }
  // await mkdir(CALIBRATION_DIR, { recursive: true });
  // await writeFile(MANIFEST_PATH, JSON.stringify(contested, null, 2));
  // return contested;
  throw new Error("calibrateInChat.buildContestedManifest: not implemented");
}

/**
 * Self-label a transcript. Iga gives her own pass/fail + confidence, separate
 * from the judge prompt. Used to detect cases where the judge prompt and Iga's
 * own intuition disagree — those are the highest-signal items to surface to
 * The user.
 */
export async function selfLabel(_args: {
  transcript: string;
  criteria: string;
}): Promise<{ label: "pass" | "fail"; confidence: number }> {
  // TODO: a separate Anthropic call (Opus) with a "self-label + confidence"
  // prompt that's distinct from the judge prompt. Output schema:
  //   <label>pass|fail</label><confidence>0.0..1.0</confidence>
  throw new Error("calibrateInChat.selfLabel: not implemented");
}

/**
 * Step 6: append the user's verdict for one contested case to the canonical
 * ground-truth set. Called by the chat layer after each AskUserQuestion
 * response.
 */
export async function recordUserVerdict(verdict: UserVerdict): Promise<void> {
  await mkdir(CALIBRATION_DIR, { recursive: true });
  await appendFile(CONTESTED_PATH, JSON.stringify(verdict) + "\n", "utf8");
}

// CLI entry — `pnpm run calibrate` (no-op stub today).
if (import.meta.url === `file://${process.argv[1]}`) {
  void (async () => {
    // eslint-disable-next-line no-console
    console.log(
      "calibrateInChat: stub. Will run the contested-manifest builder once supersedence ships and the chat layer can drive AskUserQuestion."
    );
    void SCENARIOS_DIR;
    void MANIFEST_PATH;
    void readdir;
  })();
}

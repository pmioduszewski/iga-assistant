import Anthropic from "@anthropic-ai/sdk";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import type { JudgeResult } from "./types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const JUDGES_DIR = resolve(__dirname, "..", "judges");

const DEFAULT_JUDGE_MODEL = process.env.IGA_JUDGE_MODEL || "claude-opus-4-7";

/**
 * Run a judge against a transcript. Critique-then-binary pattern (Hamel, Honeycomb template).
 *
 * The judge prompt must instruct the model to emit a final block of the form:
 *
 *   <critique>...free-form reasoning...</critique>
 *   <violations>
 *     - <one violation per line, omit block entirely if none>
 *   </violations>
 *   <outcome>pass</outcome>   // or <outcome>fail</outcome>
 *
 * We parse those three tags. Anything else is returned in `raw` for debugging.
 */
export async function runJudge(args: {
  judgeName: "relevance" | "staleness";
  transcript: string;
  criteria: string;
  client?: Anthropic;
  model?: string;
}): Promise<JudgeResult> {
  const promptPath = resolve(JUDGES_DIR, `${args.judgeName}-judge.md`);
  const judgeTemplate = await readFile(promptPath, "utf8");

  const filled = judgeTemplate
    .replace("{{CRITERIA}}", args.criteria)
    .replace("{{TRANSCRIPT}}", args.transcript);

  const client = args.client ?? new Anthropic();
  const model = args.model ?? DEFAULT_JUDGE_MODEL;

  const response = await client.messages.create({
    model,
    max_tokens: 1500,
    messages: [{ role: "user", content: filled }],
  });

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("\n");

  return parseJudgeOutput(text);
}

export function parseJudgeOutput(raw: string): JudgeResult {
  const critique = match(raw, /<critique>([\s\S]*?)<\/critique>/i) ?? "";
  const violationsBlock = match(raw, /<violations>([\s\S]*?)<\/violations>/i) ?? "";
  const outcomeRaw = match(raw, /<outcome>\s*(pass|fail)\s*<\/outcome>/i);

  const violations = violationsBlock
    .split("\n")
    .map((l) => l.replace(/^\s*[-*]\s*/, "").trim())
    .filter((l) => l.length > 0);

  const outcome = outcomeRaw?.toLowerCase() === "pass" ? "pass" : "fail";

  return {
    critique: critique.trim(),
    outcome,
    violations,
    raw,
  };
}

function match(src: string, re: RegExp): string | null {
  const m = src.match(re);
  return m ? m[1] : null;
}

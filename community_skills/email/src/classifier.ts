/**
 * LLM classifier — batched Sonnet 4.5 calls via `claude -p` (headless mode).
 *
 * Why headless `claude -p` instead of direct API: the user is on a Claude MAX
 * subscription, so `claude -p` calls hit his subscription quota with zero
 * per-call billing. The API key path is a v2 problem when this engine is
 * orchestrated by the Anthropic Agent SDK runtime.
 *
 * Constraints:
 *   - batch 10-20 messages per call
 *   - per-message input: sender, subject, first 200 chars body, existing
 *     labels, snippet, has-attachment
 *   - bound each call to ≤3k input tokens (rough estimate via char count)
 *   - structured JSON array output
 *   - retry once on JSON parse failure
 */

import { spawn } from "node:child_process";
import { ClassificationArraySchema, type Classification, type GmailMessage, type TaxonomyConfig, type ThinkingLevel } from "./types.js";

const ROUGH_CHARS_PER_TOKEN = 4;
const MAX_INPUT_TOKENS_PER_CALL = 3000;
const MAX_INPUT_CHARS = MAX_INPUT_TOKENS_PER_CALL * ROUGH_CHARS_PER_TOKEN;

export const DEFAULT_MODEL = "claude-sonnet-4-6";

const THINKING_BUDGETS: Record<Exclude<ThinkingLevel, "off">, number> = {
  low: 2000,
  medium: 8000,
  high: 20000,
};

export interface ClassifyOptions {
  batchSize: number; // 10-20
  /** Override the `claude` binary path for tests. */
  claudeBin?: string;
  /** Use a fake classifier (no real LLM call) — test/dev. */
  fake?: (messages: GmailMessage[]) => Classification[];
  /** Timeout per claude call (ms). Default 60s. */
  timeoutMs?: number;
  /** Override default model. */
  model?: string;
  /** Extended thinking level (default: off). */
  thinking?: ThinkingLevel;
}

export function resolveModel(opt?: string): string {
  return opt ?? process.env.IGA_MODEL ?? DEFAULT_MODEL;
}

export function resolveThinking(opt?: ThinkingLevel): ThinkingLevel {
  if (opt) return opt;
  const env = (process.env.IGA_THINKING ?? "").toLowerCase();
  if (env === "low" || env === "medium" || env === "high" || env === "off") return env;
  return "off";
}

export function thinkingBudget(level: ThinkingLevel): number | null {
  if (level === "off") return null;
  return THINKING_BUDGETS[level];
}

export async function classifyBatched(
  messages: GmailMessage[],
  taxonomy: TaxonomyConfig,
  opts: ClassifyOptions,
): Promise<Classification[]> {
  if (messages.length === 0) return [];

  const batches = chunkByCount(messages, Math.max(1, Math.min(20, opts.batchSize)));
  const results: Classification[] = [];

  for (const batch of batches) {
    if (opts.fake) {
      results.push(...opts.fake(batch));
      continue;
    }
    const trimmedBatch = trimBatchToBudget(batch);
    const prompt = buildPrompt(trimmedBatch, taxonomy);
    let parsed: Classification[] | null = null;
    for (let attempt = 0; attempt < 2 && !parsed; attempt++) {
      const raw = await invokeClaude(prompt, opts);
      parsed = tryParseClassifications(raw);
      if (!parsed && attempt === 0) {
        // Retry with stricter framing.
        // eslint-disable-next-line no-console
        console.error("[classifier] JSON parse failed, retrying once with stricter prompt");
      }
    }
    if (parsed) {
      results.push(...parsed);
    } else {
      // Final fallback — assign Reference + low confidence so we don't lose messages.
      for (const m of batch) {
        results.push({
          message_id: m.id,
          intent_label: "Reference",
          project_label: null,
          confidence: 0.1,
          reason: "LLM classification failed — fallback to Reference",
        });
      }
    }
  }
  return results;
}

function chunkByCount<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

/** Drop messages from the tail of the batch until the prompt fits the budget. */
function trimBatchToBudget(batch: GmailMessage[]): GmailMessage[] {
  let working = [...batch];
  while (working.length > 1 && estimateChars(working) > MAX_INPUT_CHARS) {
    working.pop();
  }
  return working;
}

function estimateChars(batch: GmailMessage[]): number {
  let n = 0;
  for (const m of batch) {
    n += (m.from?.length ?? 0) + (m.subject?.length ?? 0) +
         (m.bodyPreview?.length ?? 0) + (m.snippet?.length ?? 0) +
         (m.labelNames?.join(",").length ?? 0) + 80; // framing overhead per msg
  }
  return n + 800; // overall framing
}

export function buildPrompt(batch: GmailMessage[], taxonomy: TaxonomyConfig): string {
  const items = batch.map((m, i) => {
    return [
      `MESSAGE ${i + 1}`,
      `message_id: ${m.id}`,
      `from: ${m.from}`,
      `subject: ${truncate(m.subject, 200)}`,
      `existing_labels: ${m.labelNames.join(", ") || "(none)"}`,
      `snippet: ${truncate(m.snippet, 200)}`,
      `body_preview: ${truncate(m.bodyPreview, 200)}`,
      `has_attachment: ${m.hasAttachment ? "yes" : "no"}`,
    ].join("\n");
  }).join("\n\n");

  const intentList = taxonomy.intentLabels.join(", ");
  const projectList = taxonomy.projectLabels.join(", ");

  return [
    `You are an email triage classifier. Assign exactly one intent label and at most one optional project label per message.`,
    ``,
    `Valid intent labels (choose EXACTLY ONE per message): ${intentList}`,
    `Valid project labels (optional, choose ZERO or ONE per message): ${projectList}`,
    ``,
    `Guidance:`,
    `- "Newsletter/Dev" — developer/engineering newsletters (TypeScript, React, AI tooling, etc.)`,
    `- "Newsletter/Business" — founder/SaaS/commerce newsletters`,
    `- "Newsletter/Design" — design newsletters`,
    `- "Newsletter/News" — general/world news newsletters`,
    `- "Promo" — marketing or promotional blast (not a newsletter)`,
    `- "Status" — bot/system notifications, DMARC reports, automated updates`,
    `- "Order" — shipping/order notifications`,
    `- "Action" — message needs the user to do something`,
    `- "Wait" — message indicates the user is waiting on someone`,
    `- "Family" — family communication`,
    `- "Receipt" — invoice/receipt/payment confirmation`,
    ``,
    `For each message return a JSON object with:`,
    `  message_id (string), intent_label (string from list), project_label (string from list OR null),`,
    `  confidence (number 0-1), reason (string, ≤120 chars).`,
    ``,
    `Output ONLY a JSON array, no prose, no markdown fences. Example shape:`,
    `[{"message_id":"...","intent_label":"Newsletter/Dev","project_label":"Acme","confidence":0.9,"reason":"..."}]`,
    ``,
    `Messages to classify:`,
    ``,
    items,
  ].join("\n");
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length <= n ? s : `${s.slice(0, n)}…`;
}

export function tryParseClassifications(raw: string): Classification[] | null {
  // Strip code fences if model added them despite instructions.
  const cleaned = raw
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```\s*$/i, "")
    .trim();
  // Find the first '[' and last ']' to be robust to leading/trailing prose.
  const start = cleaned.indexOf("[");
  const end = cleaned.lastIndexOf("]");
  if (start === -1 || end === -1 || end <= start) return null;
  const slice = cleaned.slice(start, end + 1);
  try {
    const obj = JSON.parse(slice);
    const result = ClassificationArraySchema.safeParse(obj);
    if (!result.success) return null;
    return result.data;
  } catch {
    return null;
  }
}

async function invokeClaudeOnce(prompt: string, opts: ClassifyOptions): Promise<string> {
  const bin = opts.claudeBin ?? "claude";
  const timeoutMs = opts.timeoutMs ?? 60_000;
  const model = resolveModel(opts.model);
  const thinking = resolveThinking(opts.thinking);
  const budget = thinkingBudget(thinking);
  const args = ["-p", "--model", model, "--output-format", "text"];
  if (budget !== null) args.push("--max-thinking-tokens", String(budget));
  return await new Promise<string>((resolve, reject) => {
    const proc = spawn(bin, args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      reject(new Error(`claude -p timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    proc.stdout.on("data", (d) => { stdout += d.toString(); });
    proc.stderr.on("data", (d) => { stderr += d.toString(); });
    proc.on("error", (err) => { clearTimeout(timer); reject(err); });
    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        const tail = stderr.trim().slice(-500) || "(empty stderr)";
        reject(new Error(`claude -p exited ${code}: ${tail}`));
      } else {
        resolve(stdout);
      }
    });
    proc.stdin.write(prompt);
    proc.stdin.end();
  });
}

async function invokeClaude(prompt: string, opts: ClassifyOptions): Promise<string> {
  // One retry with backoff hardens against transient `claude -p` failures
  // (cold-start auth race at 06:00, brief network blip, empty-stderr exit 1).
  // The 06:05 auto-run regularly hit these on first invocation of the day.
  let lastErr: unknown;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return await invokeClaudeOnce(prompt, opts);
    } catch (err) {
      lastErr = err;
      if (attempt === 0) {
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

/**
 * Newsletter Research hook — v1 manual mode.
 *
 * Spec: rules/hooks/newsletter-research.md
 *
 * v1 behavior:
 *   - Fetch the full body of the triggered message (text format)
 *   - Print a structured "research-ready" digest to stdout so Iga (the
 *     conversational layer) can run the full artifact-extract + fit-score +
 *     MemPalace-file pipeline in a separate turn
 *
 * v2 (out of scope here):
 *   - Direct artifact extraction via `claude -p`
 *   - Per-link WebFetch (≤5 URLs/message)
 *   - MemPalace `vault/<project>` filing with `mempalace_add_drawer`
 *   - Fit-scoring against MemPalace `projects/*` semantic match
 *
 * Why digest-only in v1: this engine MUST NOT call MemPalace tools per
 * project rules — coding sessions polluting MemPalace is forbidden. The
 * conversational Iga layer (which runs outside this CLI) does the filing.
 */

import { readBody } from "../gmail.js";
import { loadAccounts } from "../config-loader.js";
import type { HookSpec, HookRunResult, TriageDecision } from "../types.js";

export async function runNewsletterResearch(
  decision: TriageDecision,
  _spec: HookSpec,
): Promise<HookRunResult> {
  const { message } = decision;
  // Resolve the account address (gmail.readBody wants the address, not alias).
  const { accounts } = await loadAccounts();
  const account = accounts.find((a) => a.alias === message.account);
  const accountEmail = account?.email ?? message.account;

  let body: { subject: string; body: string };
  try {
    body = await readBody(message.account, accountEmail, message.id, "text");
  } catch (err) {
    return {
      hookName: "newsletter-research",
      messageId: message.id,
      status: "error",
      detail: `readBody failed: ${(err as Error).message}`,
    };
  }

  // Extract URLs from body (simple regex — good enough for v1 surfacing).
  const urls = extractUrls(body.body).slice(0, 10);

  // Print a structured digest to stdout. Caller (Iga conversational layer)
  // picks this up to run the artifact-extract + fit-score loop.
  // eslint-disable-next-line no-console
  console.log(JSON.stringify({
    hook: "newsletter-research",
    messageId: message.id,
    account: message.account,
    intent: decision.intent,
    from: message.from,
    subject: message.subject,
    urls,
    bodyChars: body.body.length,
    bodyPreview: body.body.slice(0, 600),
    instruction: "v1 stops here. Iga conversational layer should: extract artifacts, fit-score against MemPalace projects/*, file ≥2-fit findings to vault/<project> with mempalace_add_drawer.",
  }));

  return {
    hookName: "newsletter-research",
    messageId: message.id,
    status: "ok",
    detail: `Emitted research digest (${urls.length} URLs, ${body.body.length} chars body)`,
  };
}

function extractUrls(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const re = /https?:\/\/[^\s<>"')]+/g;
  for (const m of text.matchAll(re)) {
    const u = m[0].replace(/[.,;:!?)]+$/, ""); // trim trailing punctuation
    if (!seen.has(u)) {
      seen.add(u);
      out.push(u);
    }
  }
  return out;
}

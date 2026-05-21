/**
 * Hook runner — discovers and dispatches hooks defined in `rules/hooks/*.md`.
 *
 * Each hook is a markdown file. The frontmatter is informal: we parse the
 * top-of-file `## Trigger` section for sub-label triggers. The matching
 * implementation lives in `src/hooks/<hook-name>.ts` — a TypeScript handler
 * that knows what to do with a triggered message.
 *
 * In v1, dispatch is **manual** — only runs when `--run-hooks` is passed to
 * the CLI. Auto-trigger on label-applied is a v2 concern.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import type { HookSpec, HookRunResult, TriageDecision } from "./types.js";
import { runNewsletterResearch } from "./hooks/newsletter-research.js";

const DEFAULT_HOOKS_DIR = path.resolve(
  process.env.IGA_HOOKS_DIR ?? path.join(process.cwd(), "rules", "hooks"),
);

type HookHandler = (decision: TriageDecision, spec: HookSpec) => Promise<HookRunResult>;

/** Registry: hook name → handler. Add new hooks here. */
const HANDLERS: Record<string, HookHandler> = {
  "newsletter-research": runNewsletterResearch,
};

export async function discoverHooks(hooksDir: string = DEFAULT_HOOKS_DIR): Promise<HookSpec[]> {
  let entries: string[] = [];
  try {
    entries = await fs.readdir(hooksDir);
  } catch {
    return [];
  }
  const hooks: HookSpec[] = [];
  for (const f of entries) {
    if (!f.endsWith(".md")) continue;
    const name = f.replace(/\.md$/, "");
    const raw = await fs.readFile(path.join(hooksDir, f), "utf8");
    hooks.push({
      name,
      triggers: parseTriggers(raw),
      enabled: HANDLERS[name] !== undefined,
      rawConfig: raw,
    });
  }
  return hooks;
}

/**
 * Extract sub-label triggers from the `## Trigger` section.
 * Conservative: looks for backticked tokens after "Sub-labels enabled:" line.
 */
export function parseTriggers(md: string): string[] {
  const triggerSection = md.match(/##\s+Trigger[^\n]*\n([\s\S]*?)(?=\n##\s|$)/i);
  if (!triggerSection) return [];
  const body = triggerSection[1]!;

  // Look for "Sub-labels enabled:" line and collect backticks from it.
  const enabledLine = body.match(/sub-labels?\s+enabled:?([^\n]+)/i);
  if (enabledLine) {
    const tokens = [...enabledLine[1]!.matchAll(/`([^`]+)`/g)].map((m) => m[1]!);
    if (tokens.length) return tokens;
  }

  // Fallback: collect all backticked tokens in the Trigger section.
  return [...body.matchAll(/`([^`]+)`/g)]
    .map((m) => m[1]!)
    .filter((s) => !/disabled/i.test(s));
}

export async function dispatchHooks(
  decisions: TriageDecision[],
  hooksDir: string = DEFAULT_HOOKS_DIR,
): Promise<HookRunResult[]> {
  const hooks = await discoverHooks(hooksDir);
  const results: HookRunResult[] = [];
  for (const hook of hooks) {
    if (!hook.enabled) continue;
    const handler = HANDLERS[hook.name]!;
    for (const decision of decisions) {
      if (!hook.triggers.includes(decision.intent)) continue;
      try {
        const result = await handler(decision, hook);
        results.push(result);
      } catch (err) {
        results.push({
          hookName: hook.name,
          messageId: decision.message.id,
          status: "error",
          detail: (err as Error).message,
        });
      }
    }
  }
  return results;
}

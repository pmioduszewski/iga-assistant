/**
 * Fixture loader for tier-2 mock-MCP runs.
 *
 * Loads canned tool responses keyed by `(tool_name, args_hash)`. A scenario
 * declares a per-tool list of fixtures, each with a `match` predicate against
 * the tool-call args; the first matching fixture wins. The same call + args
 * therefore always returns the same data — deterministic, reproducible.
 *
 * If no fixture matches a tool call, we return a benign "no results" envelope
 * so Iga can keep reasoning. The router records the miss so the test can
 * assert on it if needed.
 */
import { readFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_ROOT = resolve(__dirname, "..", "fixtures", "mcp-responses");

/**
 * One fixture file shape (JSON on disk):
 *   {
 *     "tool": "mempalace_search",
 *     "match": { "query_contains_any": ["acme", "beacon"] },
 *     "response": { ... arbitrary JSON Iga gets back ... }
 *   }
 *
 * The match predicate is intentionally tiny — we don't want a full DSL. The
 * supported keys cover the use we have today; extend in tiny increments.
 */
export interface FixtureFile {
  tool: string;
  /** Optional human-readable id for debugging / assertions. */
  id?: string;
  match?: MatchPredicate;
  response: unknown;
}

export interface MatchPredicate {
  /** All listed substrings must appear (case-insensitive) somewhere in JSON-stringified args. */
  args_contains_all?: string[];
  /** At least one listed substring must appear. */
  args_contains_any?: string[];
  /** Convenience: case-insensitive substring match against args.query specifically. */
  query_contains_any?: string[];
  /** Convenience: matches when args.query is missing or empty. */
  query_empty?: boolean;
}

export async function loadFixtureFile(relPath: string): Promise<FixtureFile> {
  const abs = resolve(FIXTURES_ROOT, relPath);
  const raw = await readFile(abs, "utf8");
  return JSON.parse(raw) as FixtureFile;
}

/**
 * Pick the first fixture that matches the given args. If none match, return
 * the first fixture without a `match` predicate (treated as default). If
 * still nothing, return `null` and let the router synthesize a benign empty.
 */
export function pickFixture(
  fixtures: FixtureFile[],
  toolName: string,
  args: unknown,
): FixtureFile | null {
  const candidates = fixtures.filter((f) => f.tool === toolName);
  for (const f of candidates) {
    if (f.match && matches(f.match, args)) return f;
  }
  // Fall back to first unguarded fixture for the tool, if any.
  return candidates.find((f) => !f.match) ?? null;
}

function matches(pred: MatchPredicate, args: unknown): boolean {
  const argsJson = JSON.stringify(args ?? {}).toLowerCase();
  const query = ((args as Record<string, unknown>)?.query ?? "")
    .toString()
    .toLowerCase();

  if (pred.args_contains_all) {
    if (!pred.args_contains_all.every((s) => argsJson.includes(s.toLowerCase()))) {
      return false;
    }
  }
  if (pred.args_contains_any) {
    if (!pred.args_contains_any.some((s) => argsJson.includes(s.toLowerCase()))) {
      return false;
    }
  }
  if (pred.query_contains_any) {
    if (!pred.query_contains_any.some((s) => query.includes(s.toLowerCase()))) {
      return false;
    }
  }
  if (pred.query_empty === true) {
    if (query.trim().length > 0) return false;
  }
  return true;
}

/**
 * Loads taxonomy + account config from `rules/email/*.md` at runtime.
 *
 * The engine is OSS-clean — it doesn't know about the user's accounts or label
 * preferences statically. Everything is parsed from markdown tables here.
 *
 * Parsing is pragmatic and tolerant: we look for known table headers and
 * extract rows. If the rules files are missing, we fall back to sane defaults
 * so the engine can still run for smoke tests.
 */

import { promises as fs } from "node:fs";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { resolveColor } from "./google/label-colors.js";
import type { AccountConfig, LabelColorSpec, SenderRule, TaxonomyConfig } from "./types.js";

const MAX_WALK_LEVELS = 6;

export function resolveRulesDir(): string {
  if (process.env.IGA_RULES_DIR) return path.resolve(process.env.IGA_RULES_DIR);
  const start = process.cwd();
  const home = os.homedir();
  let cur = start;
  for (let i = 0; i <= MAX_WALK_LEVELS; i++) {
    const candidate = path.join(cur, "rules", "email", "accounts.md");
    if (existsSync(candidate)) return path.join(cur, "rules", "email");
    if (cur === home || cur === path.dirname(cur)) break;
    cur = path.dirname(cur);
  }
  return path.join(start, "rules", "email");
}

const DEFAULT_TAXONOMY: TaxonomyConfig = {
  intentLabels: [
    "Action", "Wait", "Family", "Security", "Receipt",
    "Status", "Newsletter/Dev", "Newsletter/Design",
    "Newsletter/News", "Newsletter/Business",
    "Promo", "Reference", "Domain", "Domain/AfterMarket",
    "Order", "Accountant",
  ],
  projectLabels: ["Acme", "Globex", "Umbrella", "Personal", "Iga"],
  inboxStays: new Set(["Action", "Wait", "Family", "Security", "Receipt", "Accountant"]),
  autoArchive: new Set([
    "Newsletter/Dev", "Newsletter/Design",
    "Newsletter/News", "Newsletter/Business", "Promo",
  ]),
  promoDomains: new Set<string>(),
  labelColors: new Map<string, LabelColorSpec>(),
};

const DEFAULT_ACCOUNTS: AccountConfig[] = [];

async function readFileSafe(p: string): Promise<string | null> {
  try {
    return await fs.readFile(p, "utf8");
  } catch {
    return null;
  }
}

/**
 * Extract markdown table rows. Returns array of cell arrays per row, with the
 * header row included as the first entry (separator rows are skipped).
 * Tolerant: ignores tables that don't have at least 2 separator rows.
 */
function parseMarkdownTables(md: string): string[][][] {
  const tables: string[][][] = [];
  const lines = md.split("\n");
  let i = 0;
  while (i < lines.length) {
    if (lines[i]?.includes("|") && lines[i + 1]?.match(/^\s*\|?\s*[-: ]+\|/)) {
      const header = splitRow(lines[i]!);
      const rows: string[][] = [header];
      i += 2;
      while (i < lines.length && lines[i]?.includes("|")) {
        rows.push(splitRow(lines[i]!));
        i++;
      }
      if (rows.length > 1) tables.push(rows);
    } else {
      i++;
    }
  }
  return tables;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((s) => s.trim());
}

export async function loadTaxonomy(rulesDir: string = resolveRulesDir()): Promise<TaxonomyConfig> {
  const md = await readFileSafe(path.join(rulesDir, "taxonomy.md"));
  if (!md) return cloneTaxonomy(DEFAULT_TAXONOMY);

  const tables = parseMarkdownTables(md);
  const cfg: TaxonomyConfig = {
    intentLabels: [],
    projectLabels: [...DEFAULT_TAXONOMY.projectLabels],
    inboxStays: new Set(),
    autoArchive: new Set(),
    promoDomains: new Set(),
    labelColors: new Map<string, LabelColorSpec>(),
  };

  // Find the intent label table — first column "Label", presence of "Inbox stays?" header.
  for (const t of tables) {
    const header = t[0]!.map((h) => h.toLowerCase());
    if (header[0] === "label" && header.includes("inbox stays?")) {
      const inboxIdx = header.indexOf("inbox stays?");
      const archIdx = header.indexOf("day-1 auto-archive?");
      const colorIdx = header.indexOf("color");
      for (let r = 1; r < t.length; r++) {
        const row = t[r]!;
        const label = stripCode(row[0] ?? "");
        if (!label) continue;
        cfg.intentLabels.push(label);
        if (inboxIdx > -1 && /yes/i.test(row[inboxIdx] ?? "")) cfg.inboxStays.add(label);
        if (archIdx > -1 && /yes/i.test(row[archIdx] ?? "")) cfg.autoArchive.add(label);
        if (colorIdx > -1) {
          const colorCell = stripCode(row[colorIdx] ?? "");
          const parsed = tryParseColorCell(colorCell, label);
          if (parsed) cfg.labelColors!.set(label, parsed);
        }
      }
    }
  }

  // Project labels: prefer a dedicated table with optional color column;
  // fall back to backticked tokens in prose under `## Project labels`.
  const projectsFromTable = parseProjectLabelTable(tables, cfg.labelColors!);
  if (projectsFromTable.length > 0) {
    cfg.projectLabels = projectsFromTable;
  } else {
    const projMatch = md.match(/##\s+Project labels[^\n]*\n([\s\S]*?)(?=\n##\s|$)/i);
    if (projMatch) {
      const projects = [...projMatch[1]!.matchAll(/`([^`]+)`/g)].map((m) => m[1]!);
      if (projects.length) cfg.projectLabels = projects;
    }
  }

  // Fall back if intent label parsing failed.
  if (cfg.intentLabels.length === 0) {
    cfg.intentLabels = [...DEFAULT_TAXONOMY.intentLabels];
    cfg.inboxStays = new Set(DEFAULT_TAXONOMY.inboxStays);
    cfg.autoArchive = new Set(DEFAULT_TAXONOMY.autoArchive);
  }

  return cfg;
}

function cloneTaxonomy(t: TaxonomyConfig): TaxonomyConfig {
  return {
    intentLabels: [...t.intentLabels],
    projectLabels: [...t.projectLabels],
    inboxStays: new Set(t.inboxStays),
    autoArchive: new Set(t.autoArchive),
    promoDomains: new Set(t.promoDomains),
    labelColors: new Map(t.labelColors),
  };
}

/**
 * Parse a single "color" cell. Accepts:
 *   - empty / `-` / `none` → undefined (no canonical color for this label)
 *   - named alias: "red", "blue-light"
 *   - slash hex pair: "#ffffff/#cc3a21"
 * Invalid values are warned to stderr and ignored — taxonomy.md edits should
 * never break the engine.
 */
function tryParseColorCell(raw: string, label: string): LabelColorSpec | undefined {
  const v = raw.trim();
  if (!v || v === "-" || /^none$/i.test(v)) return undefined;
  try {
    const resolved = resolveColor(v);
    return { textColor: resolved.textColor, backgroundColor: resolved.backgroundColor };
  } catch (err) {
    process.stderr.write(
      `[iga-mail] taxonomy.md: invalid color "${v}" for label "${label}" — ${(err as Error).message}\n`,
    );
    return undefined;
  }
}

/**
 * Look for a markdown table that defines project labels. Heuristic: first
 * column header is "project" or "label" AND the table appears under or after
 * a "## Project labels" heading. We don't have section context here, so we
 * accept any table whose first column is literally "Project".
 */
function parseProjectLabelTable(
  tables: string[][][],
  labelColors: Map<string, LabelColorSpec>,
): string[] {
  for (const t of tables) {
    const header = t[0]!.map((h) => h.toLowerCase());
    if (header[0] !== "project") continue;
    const colorIdx = header.indexOf("color");
    const projects: string[] = [];
    for (let r = 1; r < t.length; r++) {
      const row = t[r]!;
      const name = stripCode(row[0] ?? "");
      if (!name) continue;
      projects.push(name);
      if (colorIdx > -1) {
        const parsed = tryParseColorCell(stripCode(row[colorIdx] ?? ""), name);
        if (parsed) labelColors.set(name, parsed);
      }
    }
    if (projects.length) return projects;
  }
  return [];
}

export async function loadAccounts(
  rulesDir: string = resolveRulesDir(),
): Promise<{
  accounts: AccountConfig[];
  promoDomains: Set<string>;
  senderRules: SenderRule[];
}> {
  const md = await readFileSafe(path.join(rulesDir, "accounts.md"));
  if (!md) return { accounts: DEFAULT_ACCOUNTS, promoDomains: new Set(), senderRules: [] };

  const accounts: AccountConfig[] = [];
  const tables = parseMarkdownTables(md);
  for (const t of tables) {
    const header = t[0]!.map((h) => h.toLowerCase());
    if (header[0] === "alias" && header[1] === "address") {
      for (let r = 1; r < t.length; r++) {
        const row = t[r]!;
        const alias = stripCode(row[0] ?? "");
        const email = stripCode(row[1] ?? "");
        if (alias && email) accounts.push({ alias, email, notes: row[2] });
      }
    }
  }

  // Promo domains: find the section heading then collect all `domain.tld` backticked tokens.
  const promoMatch = md.match(/##\s+Promo-by-domain whitelist[^\n]*\n([\s\S]*?)(?=\n##\s|$)/i);
  const promoDomains = new Set<string>();
  if (promoMatch) {
    for (const m of promoMatch[1]!.matchAll(/`([a-z0-9.-]+\.[a-z]{2,})`/gi)) {
      promoDomains.add(m[1]!.toLowerCase());
    }
  }

  // Per-sender hard rules table.
  const senderRules = parseSenderRules(tables);

  return { accounts, promoDomains, senderRules };
}

export function parseSenderRules(tables: string[][][]): SenderRule[] {
  const out: SenderRule[] = [];
  for (const t of tables) {
    const header = t[0]!.map((h) => h.toLowerCase());
    const senderIdx = header.findIndex((h) => h.includes("sender pattern"));
    const actionIdx = header.findIndex((h) => h.startsWith("action"));
    if (senderIdx === -1 || actionIdx === -1) continue;
    for (let r = 1; r < t.length; r++) {
      const row = t[r]!;
      const patternCell = (row[senderIdx] ?? "").trim();
      const actionCell = (row[actionIdx] ?? "").trim();
      if (!patternCell || !actionCell) continue;
      const rules = parseSenderRuleRow(patternCell, actionCell);
      out.push(...rules);
    }
  }
  return out;
}

/** Parse one row from "Per-sender hard rules". May produce multiple rules if pattern has slash alternation. */
function parseSenderRuleRow(patternCell: string, actionCell: string): SenderRule[] {
  const { patterns, keyword } = splitPatternAndKeyword(patternCell);
  const action = parseAction(actionCell);
  const rules: SenderRule[] = [];
  for (const pat of patterns) {
    const matcher = parsePattern(pat);
    if (!matcher) continue;
    rules.push({
      name: ruleName(pat, action.intent),
      ...matcher,
      ...(keyword ? { subjectKeyword: new RegExp(keyword, "i") } : {}),
      intent: action.intent,
      extraLabels: action.extraLabels,
      star: action.star,
    });
  }
  return rules;
}

function splitPatternAndKeyword(cell: string): { patterns: string[]; keyword: string | null } {
  // Look for `<pattern>`+keyword. The keyword starts after the last backtick before "+".
  // Strategy: find all backticked tokens; check if there's a trailing "+ ..." after the last token.
  const tokens = [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1]!.trim());
  const lastTick = cell.lastIndexOf("`");
  let keyword: string | null = null;
  if (lastTick > -1) {
    const after = cell.slice(lastTick + 1).trim();
    if (after.startsWith("+")) {
      keyword = after.slice(1).trim();
    }
  }
  // Handle "/" alternation - tokens already split by backtick.
  return { patterns: tokens, keyword };
}

function parsePattern(pat: string):
  | { fromDomain: string }
  | { fromEmailPrefix: string }
  | { fromEmail: string }
  | null
{
  const p = pat.trim().toLowerCase();
  if (!p) return null;
  // *@domain  -> match by fromDomain
  if (p.startsWith("*@")) {
    const dom = p.slice(2);
    if (!dom) return null;
    return { fromDomain: dom };
  }
  // name@*  -> match by fromEmail startsWith "name@"
  if (p.endsWith("@*")) {
    const prefix = p.slice(0, -1); // includes trailing "@"
    return { fromEmailPrefix: prefix };
  }
  // *[bot]@domain.com -> match by fromEmail regex; degenerate into a "contains" check via fromEmailPrefix
  // Represent as a generic substring rule using fromEmailPrefix... no, that's startsWith.
  // Treat *X@host as: fromEmail ends with X@host. We'll encode via a custom prefix? Easiest:
  // Convert to a regex-like check by storing in fromEmail field if no wildcard inside; else handle
  // explicitly via fromEmailPrefix when there's no leading "*", otherwise embed in subjectKeyword logic.
  if (p.includes("*")) {
    // Best-effort: pattern like "*[bot]@github.com" -> fromEmail endsWith "[bot]@github.com"
    // We don't have endsWith in SenderRule. Encode as fromDomain + a marker in the name. We'll
    // synthesize a regex via subjectKeyword? No — subject is unrelated. Add a magic "fromEmail"
    // exact-match using a sentinel that the matcher recognizes? Simplest: treat as fromDomain
    // match plus stash the literal in fromEmail field with a leading "*" preserved, and let
    // the matcher do an endsWith check when fromEmail starts with "*".
    return { fromEmail: p }; // matcher handles leading "*" as endsWith
  }
  // Exact email
  return { fromEmail: p };
}

function ruleName(pat: string, intent: string): string {
  return `${intent.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${pat.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

interface ParsedAction {
  intent: string;
  extraLabels: string[];
  star: boolean;
}

function parseAction(cell: string): ParsedAction {
  const tokens = [...cell.matchAll(/`([^`]+)`/g)].map((m) => m[1]!.trim());
  const intent = tokens[0] ?? "Reference";
  const extraLabels: string[] = [];
  for (let i = 1; i < tokens.length; i++) {
    const tok = tokens[i]!;
    if (tok.toLowerCase() === "star") continue;
    extraLabels.push(tok);
  }
  const star = /\bstar\b/i.test(cell);
  return { intent, extraLabels, star };
}

export async function loadOverrides(
  rulesDir: string = resolveRulesDir(),
): Promise<{ disabledDefaultRules: Set<string> }> {
  const md = await readFileSafe(path.join(rulesDir, "overrides.md"));
  if (!md) return { disabledDefaultRules: new Set() };
  const disabled = new Set<string>();
  const section = md.match(/##\s+Disable default rules[^\n]*\n([\s\S]*?)(?=\n##\s|$)/i);
  if (section) {
    // Accept bullet list, backticked names, or plain lines.
    for (const m of section[1]!.matchAll(/`([^`]+)`/g)) disabled.add(m[1]!.trim());
    for (const line of section[1]!.split("\n")) {
      const trimmed = line.replace(/^[-*\s]+/, "").trim();
      if (!trimmed || trimmed.startsWith("`")) continue;
      if (/^[a-z0-9][a-z0-9._-]*$/i.test(trimmed)) disabled.add(trimmed);
    }
  }
  return { disabledDefaultRules: disabled };
}

function stripCode(s: string): string {
  return s.replace(/`/g, "").trim();
}

/** Bundle helper that loads everything together. */
export async function loadConfig(rulesDir: string = resolveRulesDir()) {
  const taxonomy = await loadTaxonomy(rulesDir);
  const { accounts, promoDomains, senderRules } = await loadAccounts(rulesDir);
  const overrides = await loadOverrides(rulesDir);
  taxonomy.promoDomains = promoDomains;
  return { taxonomy, accounts, senderRules, overrides };
}

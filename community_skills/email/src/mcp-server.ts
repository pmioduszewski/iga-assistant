/**
 * MCP server surface for the Iga Email Engine.
 * Reuses the existing CLI engine — adapter only.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { triage as runTriage, type TriageReport } from "./triage.js";
import { ensureLabelsImpl, getGmailClient } from "./google/gmail-client.js";
import type {
  CanonicalLabelSpec,
  EnsureLabelsResult,
} from "./google/gmail-client.js";
import type { LabelColor } from "./google/label-colors.js";
import { loadConfig } from "./config-loader.js";
import type {
  AccountConfig,
  LabelColorSpec,
  TaxonomyConfig,
  ThinkingLevel,
  TriageOptions,
} from "./types.js";

const THINKING_VALUES = ["off", "low", "medium", "high"] as const;

type TextContent = { type: "text"; text: string };
type ToolResult = { content: TextContent[]; isError?: boolean };

function ok(value: unknown): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function fail(message: string): ToolResult {
  return { content: [{ type: "text", text: message }], isError: true };
}

async function resolveAccount(alias: string): Promise<
  { ok: true; email: string; accounts: AccountConfig[] } | { ok: false; error: string }
> {
  const { accounts } = await loadConfig();
  const found = accounts.find((a) => a.alias === alias);
  if (!found) {
    const valid = accounts.map((a) => a.alias).join(", ") || "(none configured)";
    return {
      ok: false,
      error: `Unknown account alias: "${alias}". Valid aliases: ${valid}. Configure rules/email/accounts.md to add accounts.`,
    };
  }
  return { ok: true, email: found.email, accounts };
}

function slimReport(report: TriageReport): unknown {
  return {
    ...report,
    decisions: report.decisions.map((d) => ({
      account: d.message.account,
      messageId: d.message.id,
      from: d.message.from,
      subject: d.message.subject,
      intent: d.intent,
      project: d.project ?? null,
      confidence: d.confidence,
      reason: d.reason,
      source: d.source,
      archive: d.archive,
      extras: d.extras,
    })),
  };
}

// ---------- Handlers (exported for tests) ----------

export interface TriageInput {
  account?: string[];
  maxResults?: number;
  dryRun?: boolean;
  batchSize?: number;
  runHooks?: boolean;
  model?: string;
  thinking?: ThinkingLevel;
}

export async function handleTriage(input: TriageInput): Promise<ToolResult> {
  if (input.account && input.account.length > 0) {
    const { accounts } = await loadConfig();
    const aliases = new Set(accounts.map((a) => a.alias));
    const bad = input.account.filter((a) => !aliases.has(a));
    if (bad.length > 0) {
      const valid = [...aliases].join(", ") || "(none configured)";
      return fail(
        `Unknown account alias(es): ${bad.join(", ")}. Valid aliases: ${valid}.`,
      );
    }
  }
  const opts: TriageOptions = {
    accounts: input.account ?? [],
    maxResults: input.maxResults ?? 25,
    dryRun: input.dryRun ?? true,
    runHooks: input.runHooks ?? false,
    batchSize: input.batchSize ?? 15,
    mock: process.env.IGA_EMAIL_MOCK === "1",
    ...(input.model !== undefined ? { model: input.model } : {}),
    ...(input.thinking !== undefined ? { thinking: input.thinking } : {}),
  };
  try {
    const report = await runTriage(opts);
    return ok(slimReport(report));
  } catch (err) {
    return fail(`triage failed: ${(err as Error).message}`);
  }
}

export async function handleFiltersList(input: { account: string }): Promise<ToolResult> {
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    return ok(await client.listFilters());
  } catch (err) {
    return fail(`filters_list failed: ${(err as Error).message}`);
  }
}

export interface FiltersCreateInput {
  account: string;
  criteria: {
    from?: string;
    to?: string;
    subject?: string;
    query?: string;
    hasAttachment?: boolean;
  };
  action: {
    addLabelIds?: string[];
    removeLabelIds?: string[];
    forward?: string;
  };
}

export async function handleFiltersCreate(input: FiltersCreateInput): Promise<ToolResult> {
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  const critKeys = Object.keys(input.criteria ?? {}).filter(
    (k) => (input.criteria as Record<string, unknown>)[k] !== undefined,
  );
  const actKeys = Object.keys(input.action ?? {}).filter(
    (k) => (input.action as Record<string, unknown>)[k] !== undefined,
  );
  if (critKeys.length === 0) {
    return fail("filters_create requires at least one criteria field (from, to, subject, query, hasAttachment).");
  }
  if (actKeys.length === 0) {
    return fail("filters_create requires at least one action field (addLabelIds, removeLabelIds, forward).");
  }
  try {
    const client = await getGmailClient(input.account, acc.email);
    const created = await client.createFilter(input.criteria, input.action);
    return ok(created);
  } catch (err) {
    return fail(`filters_create failed: ${(err as Error).message}`);
  }
}

export interface FiltersDeleteInput {
  account: string;
  ids: string[];
  confirm: boolean;
}

export async function handleFiltersDelete(input: FiltersDeleteInput): Promise<ToolResult> {
  if (input.confirm !== true) {
    return fail(
      "filters_delete is destructive. Pass confirm: true to proceed.",
    );
  }
  if (!input.ids || input.ids.length === 0) {
    return fail("filters_delete requires at least one id in ids[].");
  }
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    const results: Array<{ id: string; status: "deleted" | "error"; detail?: string }> = [];
    for (const id of input.ids) {
      try {
        await client.deleteFilter(id);
        results.push({ id, status: "deleted" });
      } catch (err) {
        results.push({ id, status: "error", detail: (err as Error).message });
      }
    }
    return ok({ account: input.account, results });
  } catch (err) {
    return fail(`filters_delete failed: ${(err as Error).message}`);
  }
}

export interface TrashInput {
  account: string;
  messageIds: string[];
  confirm: boolean;
}

export async function handleTrash(input: TrashInput): Promise<ToolResult> {
  // Tool is exposed as `delete` (renamed 2026-05-20). The underlying engine
  // call is batchDelete, which is a PERMANENT delete (does NOT use the Trash
  // folder). Keeping the handler name `handleTrash` for backwards-compat;
  // the externally visible MCP / CLI name is `delete`.
  if (input.confirm !== true) {
    return fail(
      "delete is PERMANENT (batchDelete bypasses Trash, NOT recoverable). Pass confirm: true to proceed.",
    );
  }
  if (!input.messageIds || input.messageIds.length === 0) {
    return fail("delete requires at least one id in messageIds[].");
  }
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    await client.batchTrash(input.messageIds);
    return ok({
      account: input.account,
      deleted: input.messageIds,
      count: input.messageIds.length,
    });
  } catch (err) {
    return fail(`delete failed: ${(err as Error).message}`);
  }
}

export interface ArchiveInput {
  account: string;
  messageIds: string[];
  markRead?: boolean;
}

export async function handleArchive(input: ArchiveInput): Promise<ToolResult> {
  if (!input.messageIds || input.messageIds.length === 0) {
    return fail("archive requires at least one id in messageIds[].");
  }
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    // Non-destructive, recoverable, works under gmail.modify (no full-mailbox
    // scope needed, unlike trash/batchDelete). Removes INBOX (archive) and,
    // unless markRead===false, UNREAD (mark read).
    const removeLabelIds =
      input.markRead === false ? ["INBOX"] : ["INBOX", "UNREAD"];
    const items = input.messageIds.map((messageId) => ({
      messageId,
      addLabelIds: [] as string[],
      removeLabelIds,
    }));
    await client.batchApplyLabels(items);
    return ok({
      account: input.account,
      archived: input.messageIds,
      count: input.messageIds.length,
      markedRead: input.markRead !== false,
    });
  } catch (err) {
    return fail(`archive failed: ${(err as Error).message}`);
  }
}

export async function handleRead(input: {
  account: string;
  messageId: string;
  bodyFormat?: "html" | "text";
}): Promise<ToolResult> {
  if (!input.messageId) {
    return fail("read requires a messageId.");
  }
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    const fmt = input.bodyFormat ?? "text";
    const { subject, body } = await client.readBody(input.messageId, fmt);
    return ok({
      account: input.account,
      messageId: input.messageId,
      bodyFormat: fmt,
      subject,
      body,
    });
  } catch (err) {
    return fail(`read failed: ${(err as Error).message}`);
  }
}

export interface SearchInput {
  account?: string;
  query: string;
  maxResults?: number;
}

export async function handleSearch(input: SearchInput): Promise<ToolResult> {
  if (!input.query || !input.query.trim()) {
    return fail("search requires a non-empty query (Gmail search syntax).");
  }
  const max = input.maxResults ?? 20;
  const { accounts } = await loadConfig();
  let targets: AccountConfig[];
  if (input.account && input.account !== "all") {
    const found = accounts.find((a) => a.alias === input.account);
    if (!found) {
      const valid = accounts.map((a) => a.alias).join(", ") || "(none configured)";
      return fail(
        `Unknown account alias: "${input.account}". Valid aliases: ${valid}, or "all".`,
      );
    }
    targets = [found];
  } else {
    targets = accounts;
  }
  try {
    const results: Array<{
      account: string;
      id: string;
      from: string;
      subject: string;
      date: string;
      snippet: string;
    }> = [];
    for (const a of targets) {
      const client = await getGmailClient(a.alias, a.email);
      const msgs = await client.searchMessages(input.query, max);
      for (const m of msgs) {
        results.push({
          account: a.alias,
          id: m.id,
          from: m.from,
          subject: m.subject,
          date: m.internalDate ? new Date(m.internalDate).toISOString() : "",
          snippet: m.bodyPreview,
        });
      }
    }
    results.sort((x, y) => (y.date < x.date ? -1 : y.date > x.date ? 1 : 0));
    return ok({
      query: input.query,
      accountsSearched: targets.map((t) => t.alias),
      count: results.length,
      results,
    });
  } catch (err) {
    return fail(`search failed: ${(err as Error).message}`);
  }
}

export async function handleLabelsList(input: { account: string }): Promise<ToolResult> {
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    return ok(await client.listLabels());
  } catch (err) {
    return fail(`labels_list failed: ${(err as Error).message}`);
  }
}

function canonicalLabelsFromTaxonomy(taxonomy: TaxonomyConfig): CanonicalLabelSpec[] {
  const seen = new Set<string>();
  const out: CanonicalLabelSpec[] = [];
  const colors = taxonomy.labelColors ?? new Map<string, LabelColorSpec>();
  for (const name of [...taxonomy.intentLabels, ...taxonomy.projectLabels]) {
    if (seen.has(name)) continue;
    seen.add(name);
    const c = colors.get(name);
    const spec: CanonicalLabelSpec = { name };
    if (c) spec.color = { textColor: c.textColor, backgroundColor: c.backgroundColor };
    out.push(spec);
  }
  return out;
}

export interface LabelsEnsureInput {
  account: string;
  dryRun?: boolean;
}

interface EnsureAccountReport {
  account: string;
  email: string;
  dryRun: boolean;
  canonical: number;
  result: EnsureLabelsResult;
  error?: string;
}

async function ensureForAccount(
  account: string,
  email: string,
  canonical: CanonicalLabelSpec[],
  dryRun: boolean,
): Promise<EnsureAccountReport> {
  try {
    const client = await getGmailClient(account, email);
    if (dryRun) {
      const existing = await client.listLabels();
      const byName = new Map(existing.map((l) => [l.name, l] as const));
      const result: EnsureLabelsResult = { created: [], updated: [], unchanged: [] };
      for (const spec of canonical) {
        const found = byName.get(spec.name);
        if (!found) {
          const entry: { name: string; color?: LabelColor } = { name: spec.name };
          if (spec.color) entry.color = spec.color;
          result.created.push(entry);
          continue;
        }
        if (spec.color) {
          const sameColor =
            found.color &&
            found.color.textColor.toLowerCase() === spec.color.textColor.toLowerCase() &&
            found.color.backgroundColor.toLowerCase() === spec.color.backgroundColor.toLowerCase();
          if (sameColor) {
            const entry: { name: string; color?: LabelColor } = { name: spec.name };
            if (found.color) entry.color = found.color;
            result.unchanged.push(entry);
          } else {
            const before: { color?: LabelColor } = {};
            if (found.color) before.color = found.color;
            result.updated.push({ name: spec.name, before, after: { color: spec.color } });
          }
        } else {
          const entry: { name: string; color?: LabelColor } = { name: spec.name };
          if (found.color) entry.color = found.color;
          result.unchanged.push(entry);
        }
      }
      return { account, email, dryRun, canonical: canonical.length, result };
    }
    const result = await ensureLabelsImpl(client, canonical);
    return { account, email, dryRun, canonical: canonical.length, result };
  } catch (err) {
    return {
      account,
      email,
      dryRun,
      canonical: canonical.length,
      result: { created: [], updated: [], unchanged: [] },
      error: (err as Error).message,
    };
  }
}

export async function handleLabelsEnsure(input: LabelsEnsureInput): Promise<ToolResult> {
  const { accounts, taxonomy } = await loadConfig();
  const canonical = canonicalLabelsFromTaxonomy(taxonomy);
  const dryRun = input.dryRun ?? false;

  const targets =
    input.account === "all"
      ? accounts
      : accounts.filter((a) => a.alias === input.account);
  if (targets.length === 0) {
    const valid = accounts.map((a) => a.alias).join(", ") || "(none configured)";
    return fail(
      `Unknown account "${input.account}". Use one of: ${valid}, or "all". Configure rules/email/accounts.md.`,
    );
  }
  try {
    const reports: EnsureAccountReport[] = [];
    for (const t of targets) {
      reports.push(await ensureForAccount(t.alias, t.email, canonical, dryRun));
    }
    return ok({
      dryRun,
      canonicalLabels: canonical.length,
      accounts: reports,
    });
  } catch (err) {
    return fail(`labels_ensure failed: ${(err as Error).message}`);
  }
}

export async function handleLabelsCreate(input: { account: string; name: string }): Promise<ToolResult> {
  if (!input.name || input.name.trim() === "") {
    return fail("labels_create requires a non-empty name.");
  }
  const acc = await resolveAccount(input.account);
  if (!acc.ok) return fail(acc.error);
  try {
    const client = await getGmailClient(input.account, acc.email);
    return ok(await client.createLabel(input.name));
  } catch (err) {
    return fail(`labels_create failed: ${(err as Error).message}`);
  }
}

// ---------- Server bootstrap ----------

async function readVersion(): Promise<string> {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const candidates = [
      path.resolve(here, "..", "package.json"),
      path.resolve(here, "..", "..", "package.json"),
    ];
    for (const p of candidates) {
      try {
        const txt = await fs.readFile(p, "utf8");
        const pkg = JSON.parse(txt) as { name?: string; version?: string };
        if (pkg.name === "@iga/email" && pkg.version) return pkg.version;
      } catch {
        // try next
      }
    }
  } catch {
    // ignore
  }
  return "0.0.0";
}

export async function buildServer(): Promise<McpServer> {
  const version = await readVersion();
  const server = new McpServer(
    {
      name: "iga-email",
      version,
    },
    {
      instructions:
        "Iga Email triage engine — pre-filter + Sonnet classifier + Gmail batch labeling + hooks",
    },
  );

  server.registerTool(
    "triage",
    {
      description:
        "Classify unread inbox messages (pre-filter + LLM) and optionally apply Gmail labels. Defaults to dryRun=true. Pass dryRun=false to mutate.",
      inputSchema: {
        account: z.array(z.string()).optional().describe("Account aliases to limit (omit = all)"),
        maxResults: z.number().int().positive().optional(),
        dryRun: z.boolean().optional().describe("Default true — set false to actually apply labels"),
        batchSize: z.number().int().min(1).max(50).optional(),
        runHooks: z.boolean().optional(),
        model: z.string().optional(),
        thinking: z.enum(THINKING_VALUES).optional(),
      },
    },
    async (args) => handleTriage(args as TriageInput),
  );

  server.registerTool(
    "filters_list",
    {
      description: "List Gmail filters for an account.",
      inputSchema: {
        account: z.string().describe("Account alias (e.g. work, personal)"),
      },
    },
    async (args) => handleFiltersList(args as { account: string }),
  );

  server.registerTool(
    "filters_create",
    {
      description: "Create a Gmail filter. Requires at least one criteria field and one action field.",
      inputSchema: {
        account: z.string(),
        criteria: z.object({
          from: z.string().optional(),
          to: z.string().optional(),
          subject: z.string().optional(),
          query: z.string().optional(),
          hasAttachment: z.boolean().optional(),
        }),
        action: z.object({
          addLabelIds: z.array(z.string()).optional(),
          removeLabelIds: z.array(z.string()).optional(),
          forward: z.string().optional(),
        }),
      },
    },
    async (args) => handleFiltersCreate(args as FiltersCreateInput),
  );

  server.registerTool(
    "filters_delete",
    {
      description: "Delete one or more Gmail filters. Destructive — requires confirm=true.",
      inputSchema: {
        account: z.string(),
        ids: z.array(z.string()).min(1),
        confirm: z.boolean().describe("Must be true to proceed"),
      },
    },
    async (args) => handleFiltersDelete(args as FiltersDeleteInput),
  );

  server.registerTool(
    "delete",
    {
      description:
        "PERMANENTLY delete messages via Gmail batchDelete. Bypasses Trash — messages are NOT recoverable. Requires confirm=true. For recoverable removal, use `archive` (label only) or move-to-Trash via batchModify (not exposed yet).",
      inputSchema: {
        account: z.string(),
        messageIds: z.array(z.string()).min(1),
        confirm: z.boolean().describe("Must be true to proceed — irreversible"),
      },
    },
    async (args) => handleTrash(args as TrashInput),
  );

  server.registerTool(
    "archive",
    {
      description:
        "Archive messages (remove INBOX; also UNREAD unless markRead:false) via batchModify. Non-destructive and recoverable — message stays in All Mail. Works under gmail.modify scope (no full-mailbox scope needed, unlike trash).",
      inputSchema: {
        account: z.string(),
        messageIds: z.array(z.string()).min(1),
        markRead: z
          .boolean()
          .optional()
          .describe(
            "Default true (remove from inbox AND mark read). false = remove from inbox but keep unread.",
          ),
      },
    },
    async (args) => handleArchive(args as ArchiveInput),
  );

  server.registerTool(
    "read",
    {
      description:
        "Read a single message's subject + body (text or HTML) by message id. " +
        "Read-only, non-mutating. Used by proactive newsletter/research workers " +
        "to fetch an email body for analysis after triage has labeled it. " +
        "Replaces the legacy iga-gmail `manage_email read` capability.",
      inputSchema: {
        account: z.string().describe("Account alias (work / personal / biz / umbrella)"),
        messageId: z.string().describe("Gmail message id to read"),
        bodyFormat: z
          .enum(["html", "text"])
          .optional()
          .describe("Default 'text'. 'html' for tracking-pixel-aware structured read."),
      },
    },
    async (args) =>
      handleRead(
        args as { account: string; messageId: string; bodyFormat?: "html" | "text" },
      ),
  );

  server.registerTool(
    "search",
    {
      description:
        "Search a Gmail account's FULL mailbox (read + archived, not just unread) " +
        "using Gmail search syntax, e.g. \"from:vercel.com newer_than:1y\", " +
        "\"subject:porkbun\", \"cloudflare credit\". Returns message metadata " +
        "(id, from, subject, date, snippet) sorted newest-first. Use the returned " +
        "id with `read` to fetch the body. Pass account=\"all\" (or omit) to search " +
        "every configured account (work / personal / biz / umbrella).",
      inputSchema: {
        account: z
          .string()
          .optional()
          .describe("Account alias, or \"all\"/omit for every configured account"),
        query: z.string().describe("Gmail search query string"),
        maxResults: z
          .number()
          .optional()
          .describe("Max results per account (default 20)"),
      },
    },
    async (args) => handleSearch(args as SearchInput),
  );

  server.registerTool(
    "labels_list",
    {
      description: "List Gmail labels for an account.",
      inputSchema: {
        account: z.string(),
      },
    },
    async (args) => handleLabelsList(args as { account: string }),
  );

  server.registerTool(
    "labels_ensure",
    {
      description:
        "Idempotently sync canonical labels from rules/email/taxonomy.md to Gmail. " +
        "For each canonical label: creates if missing, patches color if the canonical " +
        "color differs, otherwise leaves it alone. NEVER touches labels not declared " +
        "in taxonomy.md. Pass account=\"all\" to sync across every configured account. " +
        "Pass dryRun=true to preview without writing.",
      inputSchema: {
        account: z.string().describe("Account alias, or \"all\" for every configured account"),
        dryRun: z.boolean().optional().describe("Default false — set true to preview without writing"),
      },
    },
    async (args) => handleLabelsEnsure(args as LabelsEnsureInput),
  );

  server.registerTool(
    "labels_create",
    {
      description: "Create a new Gmail label.",
      inputSchema: {
        account: z.string(),
        name: z.string().min(1),
      },
    },
    async (args) => handleLabelsCreate(args as { account: string; name: string }),
  );

  return server;
}

async function main(): Promise<void> {
  const server = await buildServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write("iga-email MCP server listening on stdio\n");
}

const isDirectRun = (() => {
  try {
    const selfPath = fileURLToPath(import.meta.url);
    const argv1 = process.argv[1] ?? "";
    if (selfPath === argv1) return true;
    const selfBase = path.basename(selfPath).replace(/\.[tj]s$/, "");
    const argvBase = path.basename(argv1).replace(/\.[tj]s$/, "");
    return selfBase === "mcp-server" && (argvBase === "mcp-server" || argvBase === "iga-email-mcp");
  } catch {
    return false;
  }
})();

if (isDirectRun) {
  main().catch((err) => {
    process.stderr.write(`iga-email MCP failed: ${(err as Error).stack ?? (err as Error).message}\n`);
    process.exit(1);
  });
}

#!/usr/bin/env node
/**
 * `iga-mail` CLI entry point. Subcommands:
 *   triage [opts]                — classify + (optionally) label unread inbox
 *   filters list                 — list Gmail filters
 *   filters create               — create a Gmail filter
 *   filters delete <id...>       — delete one or more filters
 *   delete <id...>               — PERMANENTLY delete messages (batchDelete, bypasses Trash, not recoverable)
 *   labels list                  — list Gmail labels
 *   labels create <name>         — create a Gmail label
 *
 * Backward compat: with no subcommand, behaves like the old `triage-mail`.
 */

import { triage } from "./triage.js";
import { ensureLabelsImpl, getGmailClient } from "./google/gmail-client.js";
import type { CanonicalLabelSpec, EnsureLabelsResult } from "./google/gmail-client.js";
import { loadConfig } from "./config-loader.js";
import { runAuthFlow, listCredentialedAccounts } from "./google/auth-flow.js";
import type { LabelColorSpec, ThinkingLevel, TaxonomyConfig, TriageOptions } from "./types.js";

const TRIAGE_HELP = `iga-mail triage — Iga email triage engine

Usage:
  iga-mail triage [options]

Options:
  --account <alias>     limit to one account (repeatable). e.g. work, personal, biz, umbrella
  --max <n>             unread messages per account (default: 25)
  --batch-size <n>      LLM batch size, 10-20 (default: 15)
  --dry-run             classify but don't apply Gmail labels (default)
  --apply               actually apply labels via Gmail batchModify
  --run-hooks           dispatch matching hooks (e.g. newsletter-research)
  --mock                use mock fixtures (also: IGA_EMAIL_MOCK=1)
  --json                emit JSON report to stdout
  --model <name>        override Claude model (also: IGA_MODEL)
  --thinking <level>    extended thinking: off|low|medium|high (also: IGA_THINKING)
  -h, --help            show this help
`;

const ROOT_HELP = `iga-mail — Iga email engine

Usage:
  iga-mail <subcommand> [options]

Subcommands:
  triage                    classify + apply labels for unread inbox
  filters list              list Gmail filters
  filters create            create a Gmail filter (see --help)
  filters delete <id...>    delete one or more Gmail filters
  delete <id...>            PERMANENTLY delete messages (batchDelete; bypasses Trash, NOT recoverable)
  labels list               list Gmail labels
  labels create <name>      create a Gmail label
  labels ensure             sync canonical taxonomy labels (with colors) to Gmail
  auth --account <email>    (re)authorize a Google account via loopback OAuth
  auth --all                re-auth every account that has a credential file

Global options:
  --account <alias>         which account to operate on (required for non-triage)
  --json                    JSON output on stdout (human summary on stderr)
  -h, --help                show help

Run \`iga-mail <subcommand> --help\` for subcommand-specific options.
`;

const FILTERS_CREATE_HELP = `iga-mail filters create — create a Gmail filter

Required:
  --account <alias>

Criteria (at least one):
  --from <pattern>
  --to <pattern>
  --subject <pattern>
  --query <q>
  --has-attachment

Action (at least one):
  --add-label <id>     repeatable
  --remove-label <id>  repeatable
  --forward <addr>
`;

interface ParsedTriageArgs {
  options: TriageOptions;
  json: boolean;
  help: boolean;
}

function parseTriageArgs(argv: string[]): ParsedTriageArgs {
  const options: TriageOptions = {
    accounts: [],
    maxResults: 25,
    dryRun: true,
    runHooks: false,
    batchSize: 15,
    mock: process.env.IGA_EMAIL_MOCK === "1",
  };
  let json = false;
  let help = false;

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--help":
      case "-h":
        help = true;
        break;
      case "--account":
        options.accounts.push(argv[++i]!);
        break;
      case "--max":
        options.maxResults = Number(argv[++i]);
        break;
      case "--batch-size":
        options.batchSize = Number(argv[++i]);
        break;
      case "--dry-run":
        options.dryRun = true;
        break;
      case "--apply":
        options.dryRun = false;
        break;
      case "--run-hooks":
        options.runHooks = true;
        break;
      case "--mock":
        options.mock = true;
        process.env.IGA_EMAIL_MOCK = "1";
        break;
      case "--json":
        json = true;
        break;
      case "--model":
        options.model = argv[++i]!;
        break;
      case "--thinking": {
        const v = argv[++i]!;
        if (v !== "off" && v !== "low" && v !== "medium" && v !== "high") {
          throw new Error(`--thinking must be off|low|medium|high, got: ${v}`);
        }
        options.thinking = v as ThinkingLevel;
        break;
      }
      default:
        if (a?.startsWith("-")) {
          throw new Error(`Unknown flag: ${a}`);
        }
    }
  }

  return { options, json, help };
}

async function resolveAccountEmail(alias: string): Promise<string> {
  const { accounts } = await loadConfig();
  const found = accounts.find((a) => a.alias === alias);
  if (!found) {
    throw new Error(`Unknown account alias: ${alias}. Configure rules/email/accounts.md.`);
  }
  return found.email;
}

function parseAccountArg(argv: string[]): { account: string; rest: string[]; json: boolean; help: boolean } {
  let account: string | null = null;
  let json = false;
  let help = false;
  const rest: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--account") account = argv[++i]!;
    else if (a === "--json") json = true;
    else if (a === "--help" || a === "-h") help = true;
    else rest.push(a!);
  }
  if (!account) throw new Error("--account <alias> is required");
  return { account, rest, json, help };
}

async function cmdFiltersList(argv: string[]): Promise<void> {
  const { account, json } = parseAccountArg(argv);
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  const filters = await client.listFilters();
  if (json) {
    process.stdout.write(JSON.stringify(filters, null, 2) + "\n");
  } else {
    process.stdout.write(JSON.stringify(filters, null, 2) + "\n");
  }
  process.stderr.write(`\nlisted ${filters.length} filter(s) for ${account} (${email})\n`);
}

async function cmdFiltersCreate(argv: string[]): Promise<void> {
  let account: string | null = null;
  const criteria: Record<string, unknown> = {};
  const action: { addLabelIds?: string[]; removeLabelIds?: string[]; forward?: string } = {};
  let help = false;
  let json = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--help": case "-h": help = true; break;
      case "--account": account = argv[++i]!; break;
      case "--from": criteria.from = argv[++i]!; break;
      case "--to": criteria.to = argv[++i]!; break;
      case "--subject": criteria.subject = argv[++i]!; break;
      case "--query": criteria.query = argv[++i]!; break;
      case "--has-attachment": criteria.hasAttachment = true; break;
      case "--add-label": (action.addLabelIds ??= []).push(argv[++i]!); break;
      case "--remove-label": (action.removeLabelIds ??= []).push(argv[++i]!); break;
      case "--forward": action.forward = argv[++i]!; break;
      case "--json": json = true; break;
      default:
        if (a?.startsWith("-")) throw new Error(`Unknown flag: ${a}`);
    }
  }
  if (help) { process.stdout.write(FILTERS_CREATE_HELP); return; }
  if (!account) throw new Error("--account <alias> is required");
  if (Object.keys(criteria).length === 0) throw new Error("at least one criteria flag required");
  if (Object.keys(action).length === 0) throw new Error("at least one action flag required");
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  const created = await client.createFilter(criteria, action);
  process.stdout.write(JSON.stringify(created, null, 2) + "\n");
  process.stderr.write(`\ncreated filter ${created.id} on ${account}\n`);
  void json;
}

async function cmdFiltersDelete(argv: string[]): Promise<void> {
  const { account, rest } = parseAccountArg(argv);
  if (rest.length === 0) throw new Error("at least one filter id required");
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  const deleted: string[] = [];
  for (const id of rest) {
    await client.deleteFilter(id);
    deleted.push(id);
  }
  process.stdout.write(JSON.stringify({ deleted }, null, 2) + "\n");
  process.stderr.write(`\ndeleted ${deleted.length} filter(s) on ${account}\n`);
}

async function cmdDelete(argv: string[]): Promise<void> {
  const { account, rest } = parseAccountArg(argv);
  if (rest.length === 0) throw new Error("at least one message id required");
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  // batchTrash is misnamed in the client — it calls Gmail's batchDelete, which
  // PERMANENTLY deletes (does NOT move to Trash). Tool surface is `delete`
  // (renamed from `trash` on 2026-05-20) so the destructive nature is obvious.
  await client.batchTrash(rest);
  process.stdout.write(JSON.stringify({ deleted: rest }, null, 2) + "\n");
  process.stderr.write(`\nDELETED PERMANENTLY ${rest.length} message(s) on ${account}\n`);
}

async function cmdLabelsList(argv: string[]): Promise<void> {
  const { account } = parseAccountArg(argv);
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  const labels = await client.listLabels();
  process.stdout.write(JSON.stringify(labels, null, 2) + "\n");
  process.stderr.write(`\nlisted ${labels.length} label(s) for ${account}\n`);
}

async function cmdLabelsCreate(argv: string[]): Promise<void> {
  const { account, rest } = parseAccountArg(argv);
  if (rest.length === 0) throw new Error("label name required");
  const name = rest.join(" ");
  const email = await resolveAccountEmail(account);
  const client = await getGmailClient(account, email);
  const created = await client.createLabel(name);
  process.stdout.write(JSON.stringify(created, null, 2) + "\n");
  process.stderr.write(`\ncreated label "${name}" (${created.id}) on ${account}\n`);
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
      // Build a non-mutating shim that reuses listLabels but swallows writes,
      // mirroring what would happen if we called ensureLabels live.
      const existing = await client.listLabels();
      const byName = new Map(existing.map((l) => [l.name, l] as const));
      const result: EnsureLabelsResult = { created: [], updated: [], unchanged: [] };
      for (const spec of canonical) {
        const found = byName.get(spec.name);
        if (!found) {
          const entry: { name: string; color?: import("./google/label-colors.js").LabelColor } = {
            name: spec.name,
          };
          if (spec.color) entry.color = spec.color;
          result.created.push(entry);
          continue;
        }
        if (spec.color) {
          if (
            found.color &&
            found.color.textColor.toLowerCase() === spec.color.textColor.toLowerCase() &&
            found.color.backgroundColor.toLowerCase() === spec.color.backgroundColor.toLowerCase()
          ) {
            const entry: { name: string; color?: import("./google/label-colors.js").LabelColor } = {
              name: spec.name,
            };
            if (found.color) entry.color = found.color;
            result.unchanged.push(entry);
          } else {
            const before: { color?: import("./google/label-colors.js").LabelColor } = {};
            if (found.color) before.color = found.color;
            result.updated.push({ name: spec.name, before, after: { color: spec.color } });
          }
        } else {
          const entry: { name: string; color?: import("./google/label-colors.js").LabelColor } = {
            name: spec.name,
          };
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

async function cmdLabelsEnsure(argv: string[]): Promise<void> {
  let accountArg: string | null = null;
  let dryRun = false;
  let json = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--account") accountArg = argv[++i]!;
    else if (a === "--dry-run") dryRun = true;
    else if (a === "--json") json = true;
    else if (a === "--help" || a === "-h") {
      process.stdout.write(
        "iga-mail labels ensure — sync canonical taxonomy labels (with colors) to Gmail\n\n" +
        "Usage:\n  iga-mail labels ensure --account <alias|all> [--dry-run] [--json]\n",
      );
      return;
    } else if (a?.startsWith("-")) {
      throw new Error(`Unknown flag: ${a}`);
    }
  }
  if (!accountArg) throw new Error("--account <alias|all> is required");

  const { accounts, taxonomy } = await loadConfig();
  const canonical = canonicalLabelsFromTaxonomy(taxonomy);

  const targets =
    accountArg === "all"
      ? accounts
      : accounts.filter((a) => a.alias === accountArg);
  if (targets.length === 0) {
    throw new Error(`Unknown account alias: ${accountArg}. Configure rules/email/accounts.md.`);
  }

  const reports: EnsureAccountReport[] = [];
  for (const t of targets) {
    reports.push(await ensureForAccount(t.alias, t.email, canonical, dryRun));
  }

  const payload = {
    dryRun,
    canonicalLabels: canonical.length,
    accounts: reports,
  };
  process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
  const totals = reports.reduce(
    (acc, r) => ({
      created: acc.created + r.result.created.length,
      updated: acc.updated + r.result.updated.length,
      unchanged: acc.unchanged + r.result.unchanged.length,
    }),
    { created: 0, updated: 0, unchanged: 0 },
  );
  process.stderr.write(
    `\nlabels ensure (${dryRun ? "dry-run" : "applied"}): ` +
      `${reports.length} account(s), ` +
      `created=${totals.created}, updated=${totals.updated}, unchanged=${totals.unchanged}\n`,
  );
  void json; // JSON output is always emitted on stdout
}

async function cmdTriage(argv: string[]): Promise<void> {
  let parsed: ParsedTriageArgs;
  try {
    parsed = parseTriageArgs(argv);
  } catch (err) {
    process.stderr.write(`Error: ${(err as Error).message}\n\n`);
    process.stderr.write(TRIAGE_HELP);
    process.exit(2);
  }
  if (parsed.help) { process.stdout.write(TRIAGE_HELP); return; }

  const previewEmitted = !parsed.options.dryRun && parsed.json;
  const report = await triage(parsed.options, previewEmitted ? (r) => {
    process.stdout.write(JSON.stringify(slimReport(r), null, 2) + "\n");
    process.stderr.write(`\n--- preview above; applying now (Ctrl+C within 3s to abort) ---\n`);
    return new Promise((res) => setTimeout(res, 3000));
  } : undefined);

  const lines = [
    "",
    `iga-mail triage report:`,
    `  accounts scanned: ${report.accountsScanned}`,
    `  messages scanned: ${report.messagesScanned}`,
    `  pre-filter hits:  ${report.preFilterHits}`,
    `  llm classified:   ${report.llmClassified}`,
    `  llm fallbacks:    ${report.llmFallbacks}`,
    `  hook runs:        ${report.hookResults.length}`,
    `  dry-run:          ${report.dryRun}`,
  ];
  if (report.missingLabels.length) {
    lines.push(`  missing labels: ${report.missingLabels
      .map((m) => `${m.account}/${m.name}`).join(", ")}`);
  }
  process.stderr.write(lines.join("\n") + "\n");

  if (parsed.json && !previewEmitted) {
    process.stdout.write(JSON.stringify(slimReport(report), null, 2) + "\n");
  }
}

function slimReport(report: import("./triage.js").TriageReport) {
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

const TRIAGE_FLAG_TOKENS = new Set([
  "--account", "--max", "--batch-size", "--dry-run", "--apply", "--run-hooks",
  "--mock", "--json", "--model", "--thinking", "-h", "--help",
]);

const AUTH_HELP = `iga-mail auth — (re)authorize Google accounts via loopback OAuth

Usage:
  iga-mail auth --account <email>            re-auth one account (reuses its client_id/secret/scopes)
  iga-mail auth --all                        re-auth every account with a credential file
  iga-mail auth --account <email> --client-secrets <path.json>
                                             authorize a NEW account from a Google client secrets file

Notes:
  - Opens your browser for Google consent; writes ~/.local/share/iga-email/credentials/<slug>.json (mode 0600)
  - Use this when triage fails with "invalid_grant" (tokens revoked by a password change / sign-out)
  - After it finishes, restart the iga-email MCP (/mcp) so the server reloads the new tokens
`;

async function cmdAuth(args: string[]): Promise<void> {
  if (args.includes("-h") || args.includes("--help")) {
    process.stdout.write(AUTH_HELP);
    return;
  }
  const getOpt = (name: string): string | undefined => {
    const i = args.indexOf(name);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const clientSecretsPath = getOpt("--client-secrets");

  let emails: string[];
  if (args.includes("--all")) {
    emails = await listCredentialedAccounts();
    if (emails.length === 0) {
      throw new Error(
        "No credential files found to re-auth. Use `auth --account <email> --client-secrets <path>`.",
      );
    }
  } else {
    const account = getOpt("--account");
    if (!account) {
      throw new Error("auth requires --account <email> or --all. See `iga-mail auth --help`.");
    }
    emails = [account];
  }

  for (const email of emails) {
    process.stderr.write(`\n=== ${email} ===\n`);
    await runAuthFlow(email, { clientSecretsPath });
  }
  process.stderr.write(
    `\nDone. Re-authorized ${emails.length} account(s). Restart the iga-email MCP (/mcp) to load the new tokens.\n`,
  );
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const first = argv[0];

  // Backward-compat: if first arg is a flag we recognize as a triage option, run triage.
  if (!first || first.startsWith("-")) {
    if (first === "-h" || first === "--help") {
      process.stdout.write(ROOT_HELP);
      return;
    }
    if (!first || TRIAGE_FLAG_TOKENS.has(first)) {
      await cmdTriage(argv);
      return;
    }
  }

  switch (first) {
    case "triage":
      await cmdTriage(argv.slice(1));
      return;
    case "filters": {
      const sub = argv[1];
      const rest = argv.slice(2);
      if (sub === "list") return cmdFiltersList(rest);
      if (sub === "create") return cmdFiltersCreate(rest);
      if (sub === "delete") return cmdFiltersDelete(rest);
      throw new Error(`Unknown filters subcommand: ${sub ?? "(none)"}. Use list|create|delete.`);
    }
    case "delete":
      return cmdDelete(argv.slice(1));
    case "trash":
      // Legacy alias — `trash` was misnamed (calls batchDelete = permanent).
      // Renamed to `delete` on 2026-05-20. Keep alias for backwards compat
      // so existing scripts don't break; print a warning.
      process.stderr.write(
        "WARNING: `trash` is deprecated and misnamed (it permanently deletes). Use `delete` instead.\n",
      );
      return cmdDelete(argv.slice(1));
    case "labels": {
      const sub = argv[1];
      const rest = argv.slice(2);
      if (sub === "list") return cmdLabelsList(rest);
      if (sub === "create") return cmdLabelsCreate(rest);
      if (sub === "ensure") return cmdLabelsEnsure(rest);
      throw new Error(`Unknown labels subcommand: ${sub ?? "(none)"}. Use list|create|ensure.`);
    }
    case "auth":
      return cmdAuth(argv.slice(1));
    case "help":
      process.stdout.write(ROOT_HELP);
      return;
    default:
      throw new Error(`Unknown subcommand: ${first}. Try \`iga-mail --help\`.`);
  }
}

main().catch((err) => {
  process.stderr.write(`iga-mail failed: ${(err as Error).stack ?? (err as Error).message}\n`);
  process.exit(1);
});

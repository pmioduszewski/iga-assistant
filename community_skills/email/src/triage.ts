/**
 * Triage orchestrator.
 *
 * Flow:
 *   1. Load taxonomy + accounts from rules/email/
 *   2. For each account: fetch unread metadata
 *   3. Pre-filter (deterministic rules, first-match-wins)
 *   4. Batched LLM classification for everything pre-filter didn't catch
 *   5. Resolve label-name → label-id per account
 *   6. Apply labels via MCP (or dry-run print)
 *   7. Optionally dispatch hooks
 */

import { loadConfig } from "./config-loader.js";
import { listUnread, batchApplyLabels } from "./gmail.js";
import { isLlmCandidate, preFilter, type PreFilterOptions } from "./pre-filter.js";
import { classifyBatched } from "./classifier.js";
import { resolveLabelNames } from "./label-resolver.js";
import { dispatchHooks } from "./hook-runner.js";
import type { BatchModifyItem } from "./google/types.js";
import type {
  AccountAlias, AccountConfig, GmailMessage, TaxonomyConfig,
  TriageDecision, TriageOptions, HookRunResult,
} from "./types.js";

export interface TriageReport {
  accountsScanned: number;
  messagesScanned: number;
  preFilterHits: number;
  llmClassified: number;
  llmFallbacks: number;
  decisions: TriageDecision[];
  missingLabels: Array<{ account: AccountAlias; name: string }>;
  hookResults: HookRunResult[];
  dryRun: boolean;
}

export type PreviewFn = (report: TriageReport) => void | Promise<void>;

export async function triage(opts: TriageOptions, preview?: PreviewFn): Promise<TriageReport> {
  const { taxonomy, accounts, senderRules, overrides } = await loadConfig();
  const preFilterOpts: PreFilterOptions = {
    senderRules,
    disabledDefaultRules: overrides.disabledDefaultRules,
  };

  const targetAccounts = opts.accounts.length
    ? accounts.filter((a) => opts.accounts.includes(a.alias))
    : accounts;

  if (targetAccounts.length === 0) {
    throw new Error(
      "No accounts to triage. Configure rules/email/accounts.md or pass --account <alias>.",
    );
  }

  const report: TriageReport = {
    accountsScanned: 0,
    messagesScanned: 0,
    preFilterHits: 0,
    llmClassified: 0,
    llmFallbacks: 0,
    decisions: [],
    missingLabels: [],
    hookResults: [],
    dryRun: opts.dryRun,
  };

  for (const account of targetAccounts) {
    const messages = await listUnread(account.alias, account.email, opts.maxResults);
    report.accountsScanned++;
    report.messagesScanned += messages.length;

    const decisions = await classifyAccount(messages, taxonomy, opts, preFilterOpts);
    for (const d of decisions) {
      if (d.source === "pre-filter") report.preFilterHits++;
      else if (d.source === "llm") report.llmClassified++;
      else report.llmFallbacks++;
    }

    report.decisions.push(...decisions);
  }

  if (preview) await preview(report);

  if (!opts.dryRun) {
    for (const account of targetAccounts) {
      const accountDecisions = report.decisions.filter(
        (d) => d.message.account === account.alias,
      );
      const missing = await applyDecisions(account, accountDecisions, taxonomy);
      for (const name of missing) {
        report.missingLabels.push({ account: account.alias, name });
      }
    }
  }

  if (opts.runHooks) {
    report.hookResults = await dispatchHooks(report.decisions);
  }

  return report;
}

async function classifyAccount(
  messages: GmailMessage[],
  taxonomy: TaxonomyConfig,
  opts: TriageOptions,
  preFilterOpts: PreFilterOptions,
): Promise<TriageDecision[]> {
  const decisions: TriageDecision[] = [];
  const llmQueue: GmailMessage[] = [];

  for (const m of messages) {
    const hit = preFilter(m, taxonomy, preFilterOpts);
    if (hit) decisions.push(hit);
    else if (isLlmCandidate(m)) llmQueue.push(m);
    else {
      decisions.push({
        message: m,
        intent: "Reference",
        confidence: 0.2,
        reason: "No signal — fallback to Reference",
        source: "fallback",
        extras: [],
        archive: false,
      });
    }
  }

  if (llmQueue.length > 0) {
    const llmResults = await classifyBatched(llmQueue, taxonomy, {
      batchSize: opts.batchSize,
      ...(opts.model ? { model: opts.model } : {}),
      ...(opts.thinking ? { thinking: opts.thinking } : {}),
    });
    const byId = new Map(llmResults.map((r) => [r.message_id, r] as const));
    for (const m of llmQueue) {
      const result = byId.get(m.id);
      if (!result) {
        decisions.push({
          message: m,
          intent: "Reference",
          confidence: 0.1,
          reason: "LLM did not return classification — fallback to Reference",
          source: "fallback",
          extras: [],
          archive: false,
        });
        continue;
      }
      decisions.push({
        message: m,
        intent: result.intent_label,
        project: result.project_label ?? null,
        confidence: result.confidence,
        reason: result.reason,
        source: "llm",
        extras: [],
        archive: taxonomy.autoArchive.has(result.intent_label),
      });
    }
  }

  return decisions;
}

/**
 * Apply labels to Gmail. Returns label names that weren't found in the account.
 */
async function applyDecisions(
  account: AccountConfig,
  decisions: TriageDecision[],
  taxonomy: TaxonomyConfig,
): Promise<string[]> {
  const missingAll = new Set<string>();
  const items: BatchModifyItem[] = [];
  for (const d of decisions) {
    const labelNamesToAdd: string[] = [d.intent];
    if (d.project) labelNamesToAdd.push(d.project);
    for (const extra of d.extras) {
      if (extra === "star") labelNamesToAdd.push("STARRED");
      else labelNamesToAdd.push(extra);
    }

    const { ids: addIds, missing: addMissing } = await resolveLabelNames(
      account.alias, account.email, labelNamesToAdd,
    );
    addMissing.forEach((m) => missingAll.add(m));

    const removeIds: string[] = [];
    if (d.archive) {
      const { ids: removeResolved } = await resolveLabelNames(
        account.alias, account.email, ["INBOX"],
      );
      removeIds.push(...removeResolved);
    }

    if (addIds.length === 0 && removeIds.length === 0) continue;
    items.push({ messageId: d.message.id, addLabelIds: addIds, removeLabelIds: removeIds });
  }

  if (items.length > 0) {
    await batchApplyLabels(account.alias, account.email, items);
  }

  void taxonomy;
  return [...missingAll];
}

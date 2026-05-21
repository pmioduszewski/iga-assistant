/**
 * Deterministic pre-filter rules. First-match-wins.
 *
 * The engine is OSS-clean — no user-specific rules live in this file.
 * User-specific per-sender rules are loaded from `rules/email/accounts.md` at
 * runtime and passed in via `senderRules`. The generic regex-based default
 * rules below can be disabled via `rules/email/overrides.md`.
 */

import type {
  GmailMessage,
  SenderRule,
  TaxonomyConfig,
  TriageDecision,
} from "./types.js";

interface Rule {
  name: string;
  test: (m: GmailMessage, t: TaxonomyConfig) => boolean;
  apply: (m: GmailMessage, t: TaxonomyConfig) => Omit<TriageDecision, "message" | "source" | "archive">;
}

const SUBJECT_RECEIPT = /faktura|invoice|payment received|paragon|fattura/i;
const SUBJECT_SECURITY = /alert bezpieczeństwa|security alert|new login|password change/i;

export const DEFAULT_RULES: Rule[] = [
  {
    name: "subject-receipt",
    test: (m) => SUBJECT_RECEIPT.test(m.subject),
    apply: () => ({
      intent: "Receipt",
      confidence: 0.95,
      reason: "Subject matches receipt/invoice regex",
      extras: [],
    }),
  },
  {
    name: "subject-security",
    test: (m) => SUBJECT_SECURITY.test(m.subject),
    apply: () => ({
      intent: "Security",
      confidence: 0.95,
      reason: "Subject matches security-alert regex",
      extras: [],
    }),
  },
  {
    name: "promo-domain-whitelist",
    test: (m, t) => t.promoDomains.has(m.fromDomain),
    apply: () => ({
      intent: "Promo",
      confidence: 0.9,
      reason: "Sender domain on promo whitelist",
      extras: [],
    }),
  },
];

export interface PreFilterOptions {
  senderRules?: SenderRule[];
  disabledDefaultRules?: Set<string>;
}

function matchSenderRule(m: GmailMessage, r: SenderRule): boolean {
  if (r.fromDomain && m.fromDomain !== r.fromDomain) return false;
  if (r.fromEmailPrefix && !m.fromEmail.startsWith(r.fromEmailPrefix)) return false;
  if (r.fromEmail) {
    if (r.fromEmail.startsWith("*")) {
      if (!m.fromEmail.endsWith(r.fromEmail.slice(1))) return false;
    } else if (m.fromEmail !== r.fromEmail) return false;
  }
  if (!r.fromDomain && !r.fromEmailPrefix && !r.fromEmail) return false;
  if (r.subjectKeyword && !r.subjectKeyword.test(m.subject)) return false;
  return true;
}

function senderRuleDecision(
  m: GmailMessage,
  r: SenderRule,
  t: TaxonomyConfig,
): TriageDecision {
  const extras: string[] = [];
  if (r.star) extras.push("star");
  for (const lbl of r.extraLabels) extras.push(lbl);
  const archive = t.autoArchive.has(r.intent);
  const summary = describeRule(r);
  return {
    message: m,
    intent: r.intent,
    confidence: 1,
    reason: `Sender rule: ${summary}`,
    source: "pre-filter",
    extras,
    archive,
  };
}

function describeRule(r: SenderRule): string {
  const parts: string[] = [];
  if (r.fromDomain) parts.push(`*@${r.fromDomain}`);
  if (r.fromEmailPrefix) parts.push(`${r.fromEmailPrefix}*`);
  if (r.fromEmail) parts.push(r.fromEmail);
  if (r.subjectKeyword) parts.push(`subject~${r.subjectKeyword.source}`);
  return parts.join(" + ") || r.name;
}

/**
 * Returns a TriageDecision if a rule matched, otherwise null (escalate to LLM).
 *
 * Order: user senderRules (highest), then enabled DEFAULT_RULES.
 */
export function preFilter(
  message: GmailMessage,
  taxonomy: TaxonomyConfig,
  opts: PreFilterOptions = {},
): TriageDecision | null {
  for (const r of opts.senderRules ?? []) {
    if (matchSenderRule(message, r)) {
      return senderRuleDecision(message, r, taxonomy);
    }
  }
  const disabled = opts.disabledDefaultRules ?? new Set<string>();
  for (const rule of DEFAULT_RULES) {
    if (disabled.has(rule.name)) continue;
    if (rule.test(message, taxonomy)) {
      const partial = rule.apply(message, taxonomy);
      const archive = taxonomy.autoArchive.has(partial.intent);
      return {
        message,
        ...partial,
        source: "pre-filter",
        archive,
      };
    }
  }
  return null;
}

/**
 * Whether this message is a candidate for LLM classification.
 * Used to short-circuit obvious junk that has no signal AND no List-Unsubscribe.
 */
export function isLlmCandidate(message: GmailMessage): boolean {
  if (message.hasListUnsubscribe) return true;
  return Boolean(message.subject || message.bodyPreview);
}

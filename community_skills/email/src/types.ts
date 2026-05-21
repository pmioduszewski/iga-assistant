/**
 * Shared types for the Iga email triage engine.
 *
 * The engine is generic — no user-specific data lives here. Account aliases,
 * label whitelists, and per-sender rules come from `rules/email/*.md` and are
 * loaded at runtime.
 */

import { z } from "zod";

// ---------- Account / config ----------

export type AccountAlias = string; // e.g. "work", "personal" — defined in rules/email/accounts.md

export interface AccountConfig {
  alias: AccountAlias;
  email: string;
  notes?: string;
}

/** A sender rule parsed from rules/email/accounts.md "Per-sender hard rules". */
export interface SenderRule {
  name: string;
  /** Match by exact domain (lowercased), used when pattern is `*@domain`. */
  fromDomain?: string;
  /** Match by fromEmail.startsWith(prefix), used when pattern is `name@*`. */
  fromEmailPrefix?: string;
  /** Match by exact fromEmail (lowercased), used when pattern is `user@host`. */
  fromEmail?: string;
  /** AND-condition: subject must match this regex (case-insensitive). */
  subjectKeyword?: RegExp;
  intent: string;
  /** Extra intents to apply (e.g. "Receipt"). */
  extraLabels: string[];
  /** Star the message. */
  star: boolean;
}

export interface LabelColorSpec {
  textColor: string;
  backgroundColor: string;
}

export interface TaxonomyConfig {
  /** Intent labels — exactly one will be assigned per message. */
  intentLabels: string[];
  /** Project labels — at most one per message, optional. */
  projectLabels: string[];
  /** Intent labels that keep the message in inbox (inbox-stays whitelist). */
  inboxStays: Set<string>;
  /** Intent labels that should be archived on day 1 (remove INBOX). */
  autoArchive: Set<string>;
  /** Promo-by-domain whitelist (lower-cased). */
  promoDomains: Set<string>;
  /**
   * Canonical colors per label name (intent + project). Only present for
   * labels that explicitly declared a color in `rules/email/taxonomy.md`.
   * Used by `iga-mail labels ensure` to keep colors consistent across
   * the user's Gmail accounts.
   *
   * Optional on the type so existing taxonomy callers (and tests) that
   * never declare colors don't need to construct an empty Map.
   */
  labelColors?: Map<string, LabelColorSpec>;
}

// ---------- Gmail message shape (subset the engine cares about) ----------

export interface GmailMessage {
  id: string;
  threadId: string;
  account: AccountAlias;
  from: string;             // raw From header: "Name <addr@example.com>"
  fromEmail: string;        // parsed: addr@example.com (lower-cased)
  fromDomain: string;       // parsed: example.com (lower-cased)
  subject: string;
  snippet: string;
  bodyPreview: string;      // first ~200 chars of plain-text body
  labelIds: string[];       // current Gmail label IDs (not names)
  labelNames: string[];     // current Gmail label names (resolved)
  hasAttachment: boolean;
  hasListUnsubscribe: boolean;
  internalDate: number;     // ms since epoch
}

// ---------- Classification result ----------

export const ClassificationSchema = z.object({
  message_id: z.string(),
  intent_label: z.string(),
  project_label: z.string().nullable().optional(),
  confidence: z.number().min(0).max(1),
  reason: z.string(),
});

export type Classification = z.infer<typeof ClassificationSchema>;

export const ClassificationArraySchema = z.array(ClassificationSchema);

export type ClassificationSource = "pre-filter" | "llm" | "fallback";

export interface TriageDecision {
  message: GmailMessage;
  intent: string;
  project?: string | null;
  confidence: number;
  reason: string;
  source: ClassificationSource;
  /** Additional one-off actions, e.g. ["star"] for accountant mail. */
  extras: string[];
  /** Whether INBOX should be removed (day-1 auto-archive). */
  archive: boolean;
}

// ---------- Hook definition (parsed from rules/hooks/*.md) ----------

export interface HookSpec {
  name: string;          // filename without extension
  triggers: string[];    // intent labels that trigger this hook
  enabled: boolean;
  rawConfig: string;     // full markdown body for the hook handler to consult
}

export interface HookRunResult {
  hookName: string;
  messageId: string;
  status: "ok" | "skipped" | "error";
  detail?: string;
}

// ---------- CLI options ----------

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export interface TriageOptions {
  accounts: AccountAlias[];      // filter — empty = all
  maxResults: number;            // unread per account
  dryRun: boolean;               // don't apply labels, just print plan
  runHooks: boolean;             // dispatch matched hooks
  batchSize: number;             // LLM batch size (10-20)
  mock: boolean;                 // use mock data instead of MCP
  model?: string;                // override claude model
  thinking?: ThinkingLevel;      // extended thinking level
}

/**
 * Thin googleapis wrapper, one per account. Direct Gmail v1 calls, no MCP.
 */

import { google, gmail_v1 } from "googleapis";
import { getOAuthClientForEmail } from "./auth.js";
import { groupBatchItems, type BatchModifyItem } from "./types.js";
import {
  colorEquals,
  isAllowedColor,
  type LabelColor,
} from "./label-colors.js";
import type { AccountAlias, GmailMessage } from "../types.js";

export interface CanonicalLabelSpec {
  name: string;
  color?: LabelColor;
}

export interface EnsureLabelsResult {
  created: Array<{ name: string; color?: LabelColor }>;
  updated: Array<{
    name: string;
    before: { color?: LabelColor };
    after: { color?: LabelColor };
  }>;
  unchanged: Array<{ name: string; color?: LabelColor }>;
}

export interface GmailLabelSummary {
  id: string;
  name: string;
  type: string;
  color?: LabelColor;
}

const METADATA_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"];
const GET_CONCURRENCY = 10;
const BATCH_MODIFY_CHUNK = 1000;

const clientCache = new Map<string, GmailClient>();

export async function getGmailClient(
  account: AccountAlias,
  email: string,
): Promise<GmailClient> {
  const key = `${account}::${email}`;
  const hit = clientCache.get(key);
  if (hit) return hit;
  const auth = await getOAuthClientForEmail(email);
  const gmail = google.gmail({ version: "v1", auth });
  const client = new GmailClient(account, email, gmail);
  clientCache.set(key, client);
  return client;
}

export function _resetGmailClientCache(): void {
  clientCache.clear();
}

export class GmailClient {
  constructor(
    public readonly account: AccountAlias,
    public readonly email: string,
    private readonly gmail: gmail_v1.Gmail,
  ) {}

  async listUnread(maxResults: number): Promise<GmailMessage[]> {
    return this.listByQuery("is:unread in:inbox", maxResults);
  }

  /**
   * List messages matching an arbitrary Gmail search query.
   * Searches all mail (incl. read + archived), not just the unread inbox.
   * `query` uses Gmail search syntax, e.g.
   *   "from:vercel.com newer_than:1y", "subject:porkbun", "cloudflare credit".
   */
  async searchMessages(query: string, maxResults: number): Promise<GmailMessage[]> {
    return this.listByQuery(query, maxResults);
  }

  private async listByQuery(q: string, maxResults: number): Promise<GmailMessage[]> {
    const listRes = await this.gmail.users.messages.list({
      userId: "me",
      q,
      maxResults,
    });
    const stubs = listRes.data.messages ?? [];
    if (stubs.length === 0) return [];

    const out: GmailMessage[] = new Array(stubs.length);
    let cursor = 0;
    const workers: Promise<void>[] = [];
    const total = stubs.length;
    const account = this.account;
    const gmail = this.gmail;

    const worker = async (): Promise<void> => {
      while (true) {
        const idx = cursor++;
        if (idx >= total) return;
        const id = stubs[idx]!.id!;
        const res = await gmail.users.messages.get({
          userId: "me",
          id,
          format: "metadata",
          metadataHeaders: METADATA_HEADERS,
        });
        out[idx] = shapeMetadataMessage(account, res.data);
      }
    };
    for (let i = 0; i < Math.min(GET_CONCURRENCY, total); i++) {
      workers.push(worker());
    }
    await Promise.all(workers);
    return out;
  }

  async listLabels(): Promise<GmailLabelSummary[]> {
    const res = await this.gmail.users.labels.list({ userId: "me" });
    const labels = res.data.labels ?? [];
    // The list endpoint omits color; we must fetch per-label to know it.
    // Only user labels can have user-set colors — system labels never do.
    const summaries: GmailLabelSummary[] = await Promise.all(
      labels.map(async (l): Promise<GmailLabelSummary> => {
        const base: GmailLabelSummary = {
          id: l.id ?? "",
          name: l.name ?? "",
          type: l.type ?? "user",
        };
        if ((l.type ?? "user") !== "user") return base;
        try {
          const full = await this.gmail.users.labels.get({
            userId: "me",
            id: base.id,
          });
          const c = full.data.color;
          if (c?.textColor && c.backgroundColor) {
            base.color = {
              textColor: c.textColor,
              backgroundColor: c.backgroundColor,
            };
          }
        } catch {
          // tolerate per-label fetch failures — return what we have
        }
        return base;
      }),
    );
    return summaries;
  }

  async readBody(
    messageId: string,
    format: "text" | "html",
  ): Promise<{ subject: string; body: string }> {
    const res = await this.gmail.users.messages.get({
      userId: "me",
      id: messageId,
      format: "full",
    });
    const payload = res.data.payload ?? {};
    const subject = findHeader(payload.headers ?? [], "Subject") ?? "";
    const mime = format === "html" ? "text/html" : "text/plain";
    const body = extractBody(payload, mime) ?? extractBody(payload, "text/plain") ?? "";
    return { subject, body };
  }

  async applyLabels(
    messageId: string,
    addLabelIds: string[],
    removeLabelIds: string[],
  ): Promise<void> {
    await this.gmail.users.messages.modify({
      userId: "me",
      id: messageId,
      requestBody: { addLabelIds, removeLabelIds },
    });
  }

  async batchApplyLabels(items: BatchModifyItem[]): Promise<void> {
    const groups = groupBatchItems(items);
    for (const group of groups) {
      if (group.addLabelIds.length === 0 && group.removeLabelIds.length === 0) continue;
      for (let i = 0; i < group.ids.length; i += BATCH_MODIFY_CHUNK) {
        const chunk = group.ids.slice(i, i + BATCH_MODIFY_CHUNK);
        await this.gmail.users.messages.batchModify({
          userId: "me",
          requestBody: {
            ids: chunk,
            addLabelIds: group.addLabelIds,
            removeLabelIds: group.removeLabelIds,
          },
        });
      }
    }
  }

  async createFilter(
    criteria: gmail_v1.Schema$FilterCriteria,
    action: gmail_v1.Schema$FilterAction,
  ): Promise<{ id: string; criteria: gmail_v1.Schema$FilterCriteria; action: gmail_v1.Schema$FilterAction }> {
    const res = await this.gmail.users.settings.filters.create({
      userId: "me",
      requestBody: { criteria, action },
    });
    return {
      id: res.data.id ?? "",
      criteria: res.data.criteria ?? {},
      action: res.data.action ?? {},
    };
  }

  async listFilters(): Promise<Array<{ id: string; criteria: gmail_v1.Schema$FilterCriteria; action: gmail_v1.Schema$FilterAction }>> {
    const res = await this.gmail.users.settings.filters.list({ userId: "me" });
    return (res.data.filter ?? []).map((f) => ({
      id: f.id ?? "",
      criteria: f.criteria ?? {},
      action: f.action ?? {},
    }));
  }

  async deleteFilter(id: string): Promise<void> {
    await this.gmail.users.settings.filters.delete({ userId: "me", id });
  }

  async batchTrash(messageIds: string[]): Promise<void> {
    for (let i = 0; i < messageIds.length; i += BATCH_MODIFY_CHUNK) {
      const chunk = messageIds.slice(i, i + BATCH_MODIFY_CHUNK);
      await this.gmail.users.messages.batchDelete({
        userId: "me",
        requestBody: { ids: chunk },
      });
    }
  }

  async createLabel(
    name: string,
    color?: LabelColor,
  ): Promise<{ id: string; name: string; color?: LabelColor }> {
    if (color && !isAllowedColor(color.textColor, color.backgroundColor)) {
      throw new Error(
        `createLabel: color ${color.textColor}/${color.backgroundColor} is not in Gmail's allowed palette`,
      );
    }
    const requestBody: gmail_v1.Schema$Label = {
      name,
      labelListVisibility: "labelShow",
      messageListVisibility: "show",
    };
    if (color) {
      requestBody.color = {
        textColor: color.textColor,
        backgroundColor: color.backgroundColor,
      };
    }
    const res = await this.gmail.users.labels.create({
      userId: "me",
      requestBody,
    });
    const out: { id: string; name: string; color?: LabelColor } = {
      id: res.data.id ?? "",
      name: res.data.name ?? name,
    };
    if (color) out.color = color;
    return out;
  }

  async updateLabel(
    labelId: string,
    patch: { name?: string; color?: LabelColor },
  ): Promise<{ id: string; name: string; color?: LabelColor }> {
    if (patch.color && !isAllowedColor(patch.color.textColor, patch.color.backgroundColor)) {
      throw new Error(
        `updateLabel: color ${patch.color.textColor}/${patch.color.backgroundColor} is not in Gmail's allowed palette`,
      );
    }
    const requestBody: gmail_v1.Schema$Label = {};
    if (patch.name !== undefined) requestBody.name = patch.name;
    if (patch.color !== undefined) {
      requestBody.color = {
        textColor: patch.color.textColor,
        backgroundColor: patch.color.backgroundColor,
      };
    }
    const res = await this.gmail.users.labels.patch({
      userId: "me",
      id: labelId,
      requestBody,
    });
    const out: { id: string; name: string; color?: LabelColor } = {
      id: res.data.id ?? labelId,
      name: res.data.name ?? patch.name ?? "",
    };
    const c = res.data.color;
    if (c?.textColor && c.backgroundColor) {
      out.color = { textColor: c.textColor, backgroundColor: c.backgroundColor };
    }
    return out;
  }

  /**
   * Idempotently synchronize the given canonical labels with Gmail.
   *
   * For each canonical entry:
   *  - If missing → create (with color if specified)
   *  - If present and color matches → unchanged
   *  - If present and color differs (or canonical declares a color but
   *    the label has none) → patch via `updateLabel`
   *
   * IMPORTANT: this method NEVER touches labels not present in `canonical`.
   * It will not delete, rename, or recolor anything the user created outside
   * the taxonomy.
   */
  async ensureLabels(canonical: CanonicalLabelSpec[]): Promise<EnsureLabelsResult> {
    return ensureLabelsImpl(this, canonical);
  }
}

/** Pure implementation, also reused by the dry-run path. Exported for tests. */
export async function ensureLabelsImpl(
  client: Pick<GmailClient, "listLabels" | "createLabel" | "updateLabel">,
  canonical: CanonicalLabelSpec[],
): Promise<EnsureLabelsResult> {
  const existing = await client.listLabels();
  const byName = new Map<string, GmailLabelSummary>();
  for (const l of existing) byName.set(l.name, l);

  const result: EnsureLabelsResult = { created: [], updated: [], unchanged: [] };

  for (const spec of canonical) {
    const found = byName.get(spec.name);
    if (!found) {
      const created = await client.createLabel(spec.name, spec.color);
      const entry: { name: string; color?: LabelColor } = { name: spec.name };
      if (created.color) entry.color = created.color;
      else if (spec.color) entry.color = spec.color;
      result.created.push(entry);
      continue;
    }
    // Present. Does color match the canonical declaration?
    if (spec.color) {
      if (colorEquals(found.color, spec.color)) {
        const entry: { name: string; color?: LabelColor } = { name: spec.name };
        if (found.color) entry.color = found.color;
        result.unchanged.push(entry);
      } else {
        // Capture before-state BEFORE the patch — the API client (or our
        // tests' mock) may mutate `found` in place.
        const before: { color?: LabelColor } = {};
        if (found.color) {
          before.color = {
            textColor: found.color.textColor,
            backgroundColor: found.color.backgroundColor,
          };
        }
        await client.updateLabel(found.id, { color: spec.color });
        result.updated.push({
          name: spec.name,
          before,
          after: { color: spec.color },
        });
      }
    } else {
      // Canonical has no color — leave whatever's there alone.
      const entry: { name: string; color?: LabelColor } = { name: spec.name };
      if (found.color) entry.color = found.color;
      result.unchanged.push(entry);
    }
  }

  return result;
}

function shapeMetadataMessage(
  account: AccountAlias,
  raw: gmail_v1.Schema$Message,
): GmailMessage {
  const headers = raw.payload?.headers ?? [];
  const fromRaw = findHeader(headers, "From") ?? "";
  const subject = findHeader(headers, "Subject") ?? "";
  const hasListUnsub = findHeader(headers, "List-Unsubscribe") !== undefined;
  const { email, domain } = parseAddr(fromRaw);
  const snippet = raw.snippet ?? "";
  return {
    id: raw.id ?? "",
    threadId: raw.threadId ?? "",
    account,
    from: fromRaw,
    fromEmail: email,
    fromDomain: domain,
    subject,
    snippet,
    bodyPreview: snippet.slice(0, 200),
    labelIds: raw.labelIds ?? [],
    labelNames: [],
    hasAttachment: hasAttachmentFlag(raw.payload),
    hasListUnsubscribe: hasListUnsub,
    internalDate: Number(raw.internalDate ?? 0),
  };
}

function findHeader(
  headers: gmail_v1.Schema$MessagePartHeader[],
  name: string,
): string | undefined {
  const target = name.toLowerCase();
  for (const h of headers) {
    if ((h.name ?? "").toLowerCase() === target) return h.value ?? undefined;
  }
  return undefined;
}

function parseAddr(raw: string): { email: string; domain: string } {
  const m = raw.match(/<([^>]+)>/);
  const email = (m ? m[1]! : raw).trim().toLowerCase();
  const at = email.lastIndexOf("@");
  const domain = at > -1 ? email.slice(at + 1) : "";
  return { email, domain };
}

function hasAttachmentFlag(payload: gmail_v1.Schema$MessagePart | undefined): boolean {
  if (!payload) return false;
  const stack: gmail_v1.Schema$MessagePart[] = [payload];
  while (stack.length) {
    const p = stack.pop()!;
    if (p.filename && p.filename.length > 0) return true;
    for (const child of p.parts ?? []) stack.push(child);
  }
  return false;
}

function extractBody(
  payload: gmail_v1.Schema$MessagePart | undefined,
  mime: string,
): string | undefined {
  if (!payload) return undefined;
  const stack: gmail_v1.Schema$MessagePart[] = [payload];
  while (stack.length) {
    const p = stack.pop()!;
    if (p.mimeType === mime && p.body?.data) {
      return Buffer.from(p.body.data, "base64url").toString("utf8");
    }
    for (const child of p.parts ?? []) stack.push(child);
  }
  return undefined;
}

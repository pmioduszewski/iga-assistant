/**
 * Gmail facade. Routes calls through the direct googleapis-backed
 * `GmailClient` (src/google/gmail-client.ts) for live accounts, and returns
 * fixtures from test/fixtures/ when IGA_EMAIL_MOCK=1.
 *
 * Independence: no MCP, no gws CLI. Refresh tokens are read from the
 * iga-email credential cache (~/.local/share/iga-email/credentials/).
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { getGmailClient } from "./google/gmail-client.js";
import type { BatchModifyItem } from "./google/types.js";
import type { AccountAlias, GmailMessage } from "./types.js";

const MOCK = process.env.IGA_EMAIL_MOCK === "1";

interface MockFixtures {
  unread: Record<AccountAlias, GmailMessage[]>;
  labels: Record<AccountAlias, Array<{ id: string; name: string }>>;
}

let mockFixturesCache: MockFixtures | null = null;

async function loadMockFixtures(): Promise<MockFixtures> {
  if (mockFixturesCache) return mockFixturesCache;
  const here = path.dirname(new URL(import.meta.url).pathname);
  const fixtureRoot = path.resolve(here, "..", "test", "fixtures");
  const unreadPath = path.join(fixtureRoot, "unread.json");
  const labelsPath = path.join(fixtureRoot, "labels.json");
  let unread: MockFixtures["unread"] = {};
  let labels: MockFixtures["labels"] = {};
  try {
    unread = JSON.parse(await fs.readFile(unreadPath, "utf8"));
  } catch { /* empty */ }
  try {
    labels = JSON.parse(await fs.readFile(labelsPath, "utf8"));
  } catch { /* empty */ }
  mockFixturesCache = { unread, labels };
  return mockFixturesCache;
}

/**
 * Parse a raw From header into { email, domain }.
 */
export function parseFrom(raw: string): { email: string; domain: string } {
  const m = raw.match(/<([^>]+)>/);
  const email = (m ? m[1] : raw).trim().toLowerCase();
  const at = email.lastIndexOf("@");
  const domain = at > -1 ? email.slice(at + 1) : "";
  return { email, domain };
}

export async function listUnread(
  account: AccountAlias,
  accountEmail: string,
  maxResults: number,
): Promise<GmailMessage[]> {
  if (MOCK) {
    const fx = await loadMockFixtures();
    return (fx.unread[account] ?? []).slice(0, maxResults);
  }
  const client = await getGmailClient(account, accountEmail);
  return client.listUnread(maxResults);
}

export async function listLabels(
  account: AccountAlias,
  accountEmail: string,
): Promise<Array<{ id: string; name: string }>> {
  if (MOCK) {
    const fx = await loadMockFixtures();
    return fx.labels[account] ?? defaultMockLabels();
  }
  const client = await getGmailClient(account, accountEmail);
  const labels = await client.listLabels();
  return labels.map(({ id, name }) => ({ id, name }));
}

export async function applyLabels(
  account: AccountAlias,
  accountEmail: string,
  messageId: string,
  addLabelIds: string[],
  removeLabelIds: string[] = [],
): Promise<void> {
  if (MOCK) return;
  const client = await getGmailClient(account, accountEmail);
  await client.applyLabels(messageId, addLabelIds, removeLabelIds);
}

export async function batchApplyLabels(
  account: AccountAlias,
  accountEmail: string,
  items: BatchModifyItem[],
): Promise<void> {
  if (items.length === 0) return;
  if (MOCK) return;
  const client = await getGmailClient(account, accountEmail);
  await client.batchApplyLabels(items);
}

export async function readBody(
  account: AccountAlias,
  accountEmail: string,
  messageId: string,
  bodyFormat: "html" | "text" = "text",
): Promise<{ subject: string; body: string }> {
  if (MOCK) {
    return { subject: "[mock]", body: "Mock body. Set IGA_EMAIL_MOCK=0 to use live MCP." };
  }
  const client = await getGmailClient(account, accountEmail);
  return client.readBody(messageId, bodyFormat);
}

function defaultMockLabels() {
  return [
    { id: "INBOX", name: "INBOX" },
    { id: "STARRED", name: "STARRED" },
    { id: "UNREAD", name: "UNREAD" },
    { id: "Label_Action", name: "Action" },
    { id: "Label_Wait", name: "Wait" },
    { id: "Label_Family", name: "Family" },
    { id: "Label_Security", name: "Security" },
    { id: "Label_Receipt", name: "Receipt" },
    { id: "Label_Accountant", name: "Accountant" },
    { id: "Label_Status", name: "Status" },
    { id: "Label_Order", name: "Order" },
    { id: "Label_Reference", name: "Reference" },
    { id: "Label_Domain", name: "Domain" },
    { id: "Label_DomainAfterMarket", name: "Domain/AfterMarket" },
    { id: "Label_NewsletterDev", name: "Newsletter/Dev" },
    { id: "Label_NewsletterDesign", name: "Newsletter/Design" },
    { id: "Label_NewsletterNews", name: "Newsletter/News" },
    { id: "Label_NewsletterBusiness", name: "Newsletter/Business" },
    { id: "Label_Promo", name: "Promo" },
    { id: "Label_Acme", name: "Acme" },
    { id: "Label_Globex", name: "Globex" },
    { id: "Label_Umbrella", name: "Umbrella" },
    { id: "Label_Personal", name: "Personal" },
    { id: "Label_Iga", name: "Iga" },
  ];
}

/**
 * Per-account label name → label ID cache.
 *
 * Gmail label IDs differ per account, so we never hardcode them. The resolver
 * fetches `labels` once per account per process and answers lookups from the
 * in-memory cache for the rest of the run.
 *
 * If a needed label doesn't exist in the account yet, we surface the missing
 * names so the caller can decide whether to create them via the MCP. v1 does
 * NOT auto-create labels — the user is expected to create the canonical set
 * upfront (one-time setup).
 */

import { listLabels } from "./gmail.js";
import type { AccountAlias } from "./types.js";

interface AccountLabelMap {
  byName: Map<string, string>; // name → id
  byId: Map<string, string>;   // id → name
}

const cache = new Map<AccountAlias, AccountLabelMap>();

export async function getLabelMap(
  account: AccountAlias,
  accountEmail: string,
): Promise<AccountLabelMap> {
  const hit = cache.get(account);
  if (hit) return hit;
  const labels = await listLabels(account, accountEmail);
  const byName = new Map<string, string>();
  const byId = new Map<string, string>();
  for (const l of labels) {
    byName.set(l.name, l.id);
    byId.set(l.id, l.name);
  }
  const map: AccountLabelMap = { byName, byId };
  cache.set(account, map);
  return map;
}

export interface ResolveResult {
  /** Label IDs that resolved successfully. */
  ids: string[];
  /** Label names that have no ID in this account. */
  missing: string[];
}

export async function resolveLabelNames(
  account: AccountAlias,
  accountEmail: string,
  names: string[],
): Promise<ResolveResult> {
  const map = await getLabelMap(account, accountEmail);
  const ids: string[] = [];
  const missing: string[] = [];
  for (const name of names) {
    const id = map.byName.get(name);
    if (id) ids.push(id);
    else missing.push(name);
  }
  return { ids, missing };
}

/** Reset cache — test-only. */
export function _resetCache(): void {
  cache.clear();
}

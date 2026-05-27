/**
 * OAuth client provisioning for Iga email engine.
 *
 * Reads OAuth refresh tokens from the iga-email credential cache at
 * ~/.local/share/iga-email/credentials/<slug>.json and returns a
 * google-auth-library OAuth2Client that automatically refreshes access tokens.
 *
 * History: tokens were originally minted by the now-removed
 * aaronsb/google-workspace-mcp tool; the cache was migrated to this
 * iga-email-owned path on 2026-05-19. This engine only ever reads it.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { OAuth2Client } from "google-auth-library";
import type { GoogleAuthorizedUser } from "./types.js";

const GWS_CREDS_DIR = path.join(
  os.homedir(),
  ".local",
  "share",
  "iga-email",
  "credentials",
);

/**
 * Convert an email address into the gws credential filename slug.
 *
 *   "User@Example.com" → "user_at_example_dot_com"
 */
export function slugForEmail(email: string): string {
  return email
    .trim()
    .toLowerCase()
    .replace(/@/g, "_at_")
    .replace(/\./g, "_dot_");
}

export function credentialPathForEmail(email: string, dir: string = GWS_CREDS_DIR): string {
  return path.join(dir, `${slugForEmail(email)}.json`);
}

export async function readCredentialFile(filePath: string): Promise<GoogleAuthorizedUser> {
  const raw = await fs.readFile(filePath, "utf8");
  const parsed = JSON.parse(raw) as Partial<GoogleAuthorizedUser>;
  if (!parsed.client_id || !parsed.client_secret || !parsed.refresh_token) {
    throw new Error(
      `Credential file ${filePath} is missing required fields (client_id, client_secret, refresh_token).`,
    );
  }
  return parsed as GoogleAuthorizedUser;
}

const clientCache = new Map<string, OAuth2Client>();

/**
 * Atomically persist a rotated refresh token back to the credential file,
 * preserving every other field. Best-effort: a failure here is swallowed (the
 * caller is an event handler that must never throw), and the worst case is the
 * next process retries.
 *
 * Re-reads the on-disk file first so a concurrent writer's other-field updates
 * are not clobbered; the write is tmp-file + rename so a reader never sees a
 * half-written credential.
 */
async function persistRefreshToken(credFile: string, newRefreshToken: string): Promise<void> {
  try {
    let onDisk: Partial<GoogleAuthorizedUser>;
    try {
      onDisk = JSON.parse(await fs.readFile(credFile, "utf8")) as Partial<GoogleAuthorizedUser>;
    } catch {
      return; // file vanished/corrupt — don't fabricate one here
    }
    if (onDisk.refresh_token === newRefreshToken) return; // already current
    const merged = { ...onDisk, refresh_token: newRefreshToken };
    const tmp = `${credFile}.tmp-${process.pid}-${Date.now()}`;
    await fs.writeFile(tmp, JSON.stringify(merged, null, 2) + "\n", { mode: 0o600 });
    await fs.rename(tmp, credFile);
  } catch {
    /* best-effort — next refresh will try again */
  }
}

/**
 * Build (or return cached) OAuth2Client for the given email address. The client
 * is preloaded with the refresh token; google-auth-library handles automatic
 * access-token refresh on each request.
 *
 * Some Google OAuth client configs (and any consent screen still in "Testing")
 * issue a NEW refresh token on each refresh and invalidate the previous one.
 * google-auth-library surfaces the replacement via the 'tokens' event but never
 * writes it back — so without the handler below the on-disk token is consumed
 * once and every later refresh fails with invalid_grant (observed 2026-05-23:
 * token valid at 17:05, dead at 17:12, disk byte-identical). Persisting the
 * rotated token keeps the credential current across processes.
 */
export async function getOAuthClientForEmail(email: string): Promise<OAuth2Client> {
  const cached = clientCache.get(email);
  if (cached) return cached;

  const credFile = credentialPathForEmail(email);
  const cred = await readCredentialFile(credFile);

  const client = new OAuth2Client({
    clientId: cred.client_id,
    clientSecret: cred.client_secret,
  });
  client.setCredentials({ refresh_token: cred.refresh_token });

  client.on("tokens", (tokens) => {
    if (tokens.refresh_token && tokens.refresh_token !== cred.refresh_token) {
      cred.refresh_token = tokens.refresh_token; // keep this process's view current
      void persistRefreshToken(credFile, tokens.refresh_token);
    }
  });

  clientCache.set(email, client);
  return client;
}

/** Drop a cached OAuth client so the next call rebuilds it from disk. */
export function evictOAuthClient(email: string): void {
  clientCache.delete(email);
}

export function _resetAuthCache(): void {
  clientCache.clear();
}

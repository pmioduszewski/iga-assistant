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
 * Build (or return cached) OAuth2Client for the given email address. The client
 * is preloaded with the refresh token; google-auth-library handles automatic
 * access-token refresh on each request.
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

  clientCache.set(email, client);
  return client;
}

export function _resetAuthCache(): void {
  clientCache.clear();
}

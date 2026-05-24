/**
 * Interactive OAuth (re-)authentication for the Iga email engine.
 *
 * The triage engine only ever READS refresh tokens (see auth.ts). When a token
 * is revoked — e.g. the user signs out of all Google sessions or changes their
 * password — there is nothing on disk to mint a fresh one (the original
 * aaronsb/google-workspace-mcp minter was removed 2026-05-19). This module is
 * the re-runnable replacement: a standard loopback ("installed app") OAuth
 * flow, one consent per Google account, writing the same `authorized_user`
 * credential file the rest of the engine reads.
 *
 * Loopback redirect (http://127.0.0.1:<ephemeral-port>) needs no pre-registered
 * redirect URI for Desktop-type OAuth clients, which is what these credentials
 * use.
 */

import { promises as fs } from "node:fs";
import http from "node:http";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import type { AddressInfo } from "node:net";
import { OAuth2Client } from "google-auth-library";
import { credentialPathForEmail, readCredentialFile } from "./auth.js";
import type { GoogleAuthorizedUser } from "./types.js";

/** Default Gmail scopes (match the existing credential set). */
const DEFAULT_SCOPES = [
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.settings.basic",
  "openid",
  "https://www.googleapis.com/auth/userinfo.email",
];

interface ClientSecrets {
  client_id: string;
  client_secret: string;
}

/** Read client_id/secret from a Google `client_secret_*.json` (installed|web). */
async function readClientSecretsFile(p: string): Promise<ClientSecrets> {
  const raw = await fs.readFile(p, "utf8");
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const block = (parsed.installed ?? parsed.web ?? parsed) as Record<string, string>;
  if (!block.client_id || !block.client_secret) {
    throw new Error(
      `${p} is not a recognizable Google client secrets file (need client_id + client_secret).`,
    );
  }
  return { client_id: block.client_id, client_secret: block.client_secret };
}

/** Reverse the credential filename slug back into an email address. */
export function emailForSlug(slug: string): string {
  return slug.replace(/_at_/g, "@").replace(/_dot_/g, ".");
}

/** List emails that already have a credential file (for `auth --all`). */
export async function listCredentialedAccounts(): Promise<string[]> {
  // credentialPathForEmail("x") → "<dir>/x.json"; strip the filename to get the dir.
  const dir = credentialPathForEmail("x").replace(/[^/]+$/, "");
  let files: string[];
  try {
    files = await fs.readdir(dir);
  } catch {
    return [];
  }
  return files
    .filter((f) => f.endsWith(".json"))
    .map((f) => emailForSlug(f.replace(/\.json$/, "")))
    .sort();
}

function openBrowser(url: string): void {
  const cmd =
    process.platform === "darwin"
      ? "open"
      : process.platform === "win32"
        ? "cmd"
        : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  try {
    spawn(cmd, args, { stdio: "ignore", detached: true }).unref();
  } catch {
    /* non-fatal — the URL is printed for manual paste */
  }
}

export interface AuthFlowOptions {
  /** Path to a Google client_secret JSON — required only for a brand-new account. */
  clientSecretsPath?: string;
  /** Where to log progress (defaults to stderr). */
  log?: (msg: string) => void;
}

/**
 * Run the interactive consent flow for one account and persist a fresh
 * `authorized_user` credential file. Reuses the existing file's
 * client_id / client_secret / scopes when present (the re-auth case);
 * otherwise requires `clientSecretsPath` (the new-account case).
 */
export async function runAuthFlow(email: string, opts: AuthFlowOptions = {}): Promise<void> {
  const log = opts.log ?? ((m: string) => process.stderr.write(m + "\n"));
  const credPath = credentialPathForEmail(email);

  let clientId: string;
  let clientSecret: string;
  let scopes: string[];

  let existing: GoogleAuthorizedUser | undefined;
  try {
    existing = await readCredentialFile(credPath);
  } catch {
    existing = undefined; // missing or incomplete → treat as new account
  }

  if (existing) {
    clientId = existing.client_id;
    clientSecret = existing.client_secret;
    scopes = existing.scopes && existing.scopes.length ? existing.scopes : DEFAULT_SCOPES;
  } else if (opts.clientSecretsPath) {
    const cs = await readClientSecretsFile(opts.clientSecretsPath);
    clientId = cs.client_id;
    clientSecret = cs.client_secret;
    scopes = DEFAULT_SCOPES;
  } else {
    throw new Error(
      `No existing credential for ${email} and no --client-secrets provided. ` +
        `Pass --client-secrets <google_client_secret.json> to authorize a new account.`,
    );
  }

  const state = crypto.randomBytes(16).toString("hex");
  let oauth: OAuth2Client | undefined;

  const code = await new Promise<string>((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const reqUrl = new URL(req.url ?? "/", "http://127.0.0.1");
      const err = reqUrl.searchParams.get("error");
      const gotCode = reqUrl.searchParams.get("code");
      if (!err && !gotCode) {
        res.writeHead(204); // favicon / stray hit — keep waiting
        res.end();
        return;
      }
      const finish = (status: number, body: string) => {
        res.writeHead(status, { "content-type": "text/html; charset=utf-8" });
        res.end(body);
        server.close();
      };
      if (err) {
        finish(400, `<h2>Authorization failed</h2><p>${err}</p><p>You can close this tab.</p>`);
        reject(new Error(`OAuth error: ${err}`));
        return;
      }
      if (reqUrl.searchParams.get("state") !== state) {
        finish(400, `<h2>State mismatch</h2><p>Possible CSRF — aborted. Close this tab and retry.</p>`);
        reject(new Error("OAuth state mismatch (CSRF guard)."));
        return;
      }
      finish(200, `<h2>✅ ${email} authorized</h2><p>Iga has a fresh token. You can close this tab.</p>`);
      resolve(gotCode as string);
    });

    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as AddressInfo).port;
      const redirectUri = `http://127.0.0.1:${port}`;
      oauth = new OAuth2Client({ clientId, clientSecret, redirectUri });
      const authUrl = oauth.generateAuthUrl({
        access_type: "offline",
        prompt: "consent", // force a refresh_token even on re-consent
        scope: scopes,
        state,
        login_hint: email,
      });
      log(`\nAuthorizing ${email}`);
      log(`Opening your browser… if it doesn't open, paste this URL:\n${authUrl}\n`);
      openBrowser(authUrl);
      log(`Waiting for consent on ${redirectUri} …`);
    });
  });

  if (!oauth) throw new Error("internal: OAuth client not initialized");
  const { tokens } = await oauth.getToken(code);
  const refresh_token = tokens.refresh_token;
  if (!refresh_token) {
    throw new Error(
      "Google returned no refresh_token. Revoke Iga's prior access at " +
        "https://myaccount.google.com/permissions and retry (prompt=consent should normally force one).",
    );
  }

  const out: GoogleAuthorizedUser = {
    type: "authorized_user",
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token,
    scopes,
  };
  await fs.writeFile(credPath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  log(`✅ Wrote ${credPath}`);
}

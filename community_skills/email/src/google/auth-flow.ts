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
  /**
   * Path to a Google client_secret JSON. Required for a brand-new account. When
   * given for an account that already has a credential file, it WINS over the
   * stored client, which is how an install moves to a different OAuth client.
   */
  clientSecretsPath?: string;
  /** Where to log progress (defaults to stderr). */
  log?: (msg: string) => void;
  /** "2 of 4" style progress for multi-account runs. */
  position?: { index: number; total: number };
  /** false = only print the URL (paste it into the browser profile you want). */
  openBrowser?: boolean;
  /** Save the token even if a different Google account signed in. */
  allowAccountMismatch?: boolean;
}

/**
 * Canonical form used ONLY to compare two addresses. Gmail ignores dots and
 * "+tags" in the local part, so both spellings reach the same mailbox.
 */
export function normalizeEmailForCompare(email: string): string {
  const e = email.trim().toLowerCase();
  const at = e.lastIndexOf("@");
  if (at < 0) return e;
  let local = e.slice(0, at);
  let domain = e.slice(at + 1);
  if (domain === "googlemail.com") domain = "gmail.com";
  if (domain === "gmail.com") local = local.split("+")[0].replace(/\./g, "");
  return `${local}@${domain}`;
}

export function sameAccount(a: string, b: string): boolean {
  return normalizeEmailForCompare(a) === normalizeEmailForCompare(b);
}

/** Which OAuth client + scopes a run uses. An explicit secrets file always wins. */
export function resolveClient(
  existing: Pick<GoogleAuthorizedUser, "client_id" | "client_secret" | "scopes"> | undefined,
  fromFile: ClientSecrets | undefined,
): { clientId: string; clientSecret: string; scopes: string[] } | undefined {
  const scopes = existing?.scopes && existing.scopes.length ? existing.scopes : DEFAULT_SCOPES;
  if (fromFile) return { clientId: fromFile.client_id, clientSecret: fromFile.client_secret, scopes };
  if (existing) return { clientId: existing.client_id, clientSecret: existing.client_secret, scopes };
  return undefined;
}

const escapeHtml = (t: string): string =>
  t.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/**
 * Run the interactive consent flow for one account and persist a fresh
 * `authorized_user` credential file. Uses `clientSecretsPath` when given,
 * otherwise the client stored in the existing credential file (plain re-auth).
 *
 * The token is only saved if the Google account that actually signed in is the
 * one that was asked for. People with several accounts pick the wrong one in
 * the account chooser all the time; without this check the wrong mailbox's
 * token would be stored silently under the right name.
 */
export async function runAuthFlow(email: string, opts: AuthFlowOptions = {}): Promise<void> {
  const log = opts.log ?? ((m: string) => process.stderr.write(m + "\n"));
  const credPath = credentialPathForEmail(email);

  let existing: GoogleAuthorizedUser | undefined;
  try {
    existing = await readCredentialFile(credPath);
  } catch {
    existing = undefined; // missing or incomplete → treat as new account
  }

  const fromFile = opts.clientSecretsPath
    ? await readClientSecretsFile(opts.clientSecretsPath)
    : undefined;
  const client = resolveClient(existing, fromFile);
  if (!client) {
    throw new Error(
      `No existing credential for ${email} and no --client-secrets provided. ` +
        `Pass --client-secrets <google_client_secret.json> to authorize a new account.`,
    );
  }
  const { clientId, clientSecret, scopes } = client;

  const state = crypto.randomBytes(16).toString("hex");
  const where = opts.position ? `${opts.position.index} of ${opts.position.total}: ` : "";

  const refresh_token = await new Promise<string>((resolve, reject) => {
    let oauth: OAuth2Client | undefined;

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
        res.end(`<body style="font-family:system-ui;max-width:34rem;margin:4rem auto">${body}</body>`);
        server.close();
      };
      const fail = (status: number, html: string, error: Error) => {
        finish(status, html);
        reject(error);
      };
      if (err) {
        fail(
          400,
          `<h2>Authorization failed</h2><p>${escapeHtml(err)}</p><p>You can close this tab.</p>`,
          new Error(`OAuth error: ${err}`),
        );
        return;
      }
      if (reqUrl.searchParams.get("state") !== state) {
        fail(
          400,
          `<h2>State mismatch</h2><p>Possible CSRF, aborted. Close this tab and retry.</p>`,
          new Error("OAuth state mismatch (CSRF guard)."),
        );
        return;
      }

      // Exchange the code BEFORE answering the browser, so the page the person
      // is looking at reports what really happened (success, or wrong account).
      void (async () => {
        if (!oauth) throw new Error("internal: OAuth client not initialized");
        const { tokens } = await oauth.getToken(gotCode as string);

        let signedIn: string | undefined;
        if (tokens.id_token) {
          const ticket = await oauth.verifyIdToken({ idToken: tokens.id_token, audience: clientId });
          signedIn = ticket.getPayload()?.email;
        }
        if (signedIn && !sameAccount(signedIn, email) && !opts.allowAccountMismatch) {
          fail(
            409,
            `<h2>Wrong Google account</h2>` +
              `<p>You signed in as <b>${escapeHtml(signedIn)}</b>, but Iga asked for ` +
              `<b>${escapeHtml(email)}</b>.</p>` +
              `<p>Nothing was saved. Close this tab and try again, choosing ` +
              `<b>${escapeHtml(email)}</b> in Google's account list.</p>`,
            new Error(
              `Wrong Google account: signed in as ${signedIn}, expected ${email}. Nothing was saved. ` +
                `Retry and pick ${email} in the account chooser ` +
                `(or pass --allow-account-mismatch if this is an alias of the same mailbox).`,
            ),
          );
          return;
        }
        if (!tokens.refresh_token) {
          fail(
            400,
            `<h2>No long-lived token</h2><p>Google did not return one. See the terminal for the fix.</p>`,
            new Error(
              "Google returned no refresh_token. Revoke Iga's prior access at " +
                "https://myaccount.google.com/permissions and retry (prompt=consent should normally force one).",
            ),
          );
          return;
        }
        finish(
          200,
          `<h2>✅ ${escapeHtml(email)} connected</h2><p>Iga has a fresh token. You can close this tab.</p>`,
        );
        resolve(tokens.refresh_token);
      })().catch((e: unknown) => {
        const error = e instanceof Error ? e : new Error(String(e));
        fail(500, `<h2>Something went wrong</h2><p>${escapeHtml(error.message)}</p>`, error);
      });
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
      log(`\n=== Authorizing ${where}${email} ===`);
      log(
        `Google will warn that it "hasn't verified this app". That is expected for a ` +
          `self-hosted app: click "Advanced", then "Go to … (unsafe)", then "Allow".`,
      );
      if (opts.openBrowser === false) {
        log(`Paste this URL into the browser profile where ${email} is signed in:\n${authUrl}\n`);
      } else {
        log(`Opening your browser… if it doesn't open, paste this URL:\n${authUrl}\n`);
        openBrowser(authUrl);
      }
      log(`Waiting for consent on ${redirectUri} …`);
    });
  });

  const out: GoogleAuthorizedUser = {
    type: "authorized_user",
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token,
    scopes,
  };
  await fs.writeFile(credPath, JSON.stringify(out, null, 2) + "\n", { mode: 0o600 });
  log(`✅ ${email} connected. Wrote ${credPath}`);
}

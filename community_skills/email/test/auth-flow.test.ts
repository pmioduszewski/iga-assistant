import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeEmailForCompare, sameAccount, resolveClient } from "../src/google/auth-flow.js";

test("normalizeEmailForCompare: case and whitespace", () => {
  assert.equal(normalizeEmailForCompare("  Alex@Example.COM "), "alex@example.com");
});

test("normalizeEmailForCompare: gmail ignores dots and +tags", () => {
  assert.equal(normalizeEmailForCompare("a.l.e.x+news@gmail.com"), "alex@gmail.com");
  assert.equal(normalizeEmailForCompare("alex@googlemail.com"), "alex@gmail.com");
});

test("normalizeEmailForCompare: other domains keep dots and tags", () => {
  assert.equal(normalizeEmailForCompare("a.lex+x@example.com"), "a.lex+x@example.com");
});

test("sameAccount: same mailbox, different spelling", () => {
  assert.equal(sameAccount("Alex.Rivera@gmail.com", "alexrivera@gmail.com"), true);
});

test("sameAccount: a different account is rejected", () => {
  assert.equal(sameAccount("alex@gmail.com", "alex.work@gmail.com"), false);
  assert.equal(sameAccount("alex@gmail.com", "alex@example.com"), false);
});

const stored = { client_id: "old-id", client_secret: "old-secret", scopes: ["scope-a"] };
const file = { client_id: "new-id", client_secret: "new-secret" };
const IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"];

test("resolveClient: plain re-auth reuses the stored client and scopes", () => {
  assert.deepEqual(resolveClient(stored, undefined), {
    clientId: "old-id", clientSecret: "old-secret", scopes: ["scope-a", ...IDENTITY],
  });
});

test("resolveClient: a client secrets file wins over the stored client, scopes are kept", () => {
  assert.deepEqual(resolveClient(stored, file), {
    clientId: "new-id", clientSecret: "new-secret", scopes: ["scope-a", ...IDENTITY],
  });
});

test("resolveClient: new account takes the file and the default scopes", () => {
  const r = resolveClient(undefined, file);
  assert.equal(r?.clientId, "new-id");
  assert.ok(r!.scopes.some((s) => s.endsWith("/gmail.modify")));
});

test("resolveClient: nothing to go on", () => {
  assert.equal(resolveClient(undefined, undefined), undefined);
});

test("resolveClient: identity scopes are added once, never duplicated", () => {
  const r = resolveClient({ ...stored, scopes: ["openid", "scope-a"] }, undefined);
  assert.deepEqual(r?.scopes, ["openid", "scope-a", "https://www.googleapis.com/auth/userinfo.email"]);
});

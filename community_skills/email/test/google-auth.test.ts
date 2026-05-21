import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  slugForEmail,
  credentialPathForEmail,
  readCredentialFile,
} from "../src/google/auth.js";

describe("slugForEmail", () => {
  it("converts @ and . to gws conventions", () => {
    assert.equal(
      slugForEmail("studio.cafe@gmail.com"),
      "studio_dot_cafe_at_gmail_dot_com",
    );
  });

  it("lowercases input", () => {
    assert.equal(
      slugForEmail("the user@Example.COM"),
      "user_at_example_dot_com",
    );
  });

  it("handles multi-dot domains", () => {
    assert.equal(
      slugForEmail("a@sub.example.co.uk"),
      "a_at_sub_dot_example_dot_co_dot_uk",
    );
  });
});

describe("credentialPathForEmail", () => {
  it("composes a path with the slug filename", () => {
    const p = credentialPathForEmail("test@gmail.com");
    assert.ok(p.endsWith("test_at_gmail_dot_com.json"), `unexpected path: ${p}`);
  });
});

describe("readCredentialFile", () => {
  it("parses a valid authorized_user JSON file", async () => {
    const tmp = path.join(os.tmpdir(), `iga-cred-${Date.now()}.json`);
    const payload = {
      type: "authorized_user",
      client_id: "cid",
      client_secret: "csecret",
      refresh_token: "rtok",
      scopes: ["scope.a"],
    };
    await fs.writeFile(tmp, JSON.stringify(payload), "utf8");
    try {
      const got = await readCredentialFile(tmp);
      assert.equal(got.client_id, "cid");
      assert.equal(got.client_secret, "csecret");
      assert.equal(got.refresh_token, "rtok");
    } finally {
      await fs.unlink(tmp);
    }
  });

  it("rejects credentials missing refresh_token", async () => {
    const tmp = path.join(os.tmpdir(), `iga-cred-bad-${Date.now()}.json`);
    await fs.writeFile(tmp, JSON.stringify({ client_id: "x", client_secret: "y" }));
    try {
      await assert.rejects(() => readCredentialFile(tmp), /missing required fields/);
    } finally {
      await fs.unlink(tmp);
    }
  });
});

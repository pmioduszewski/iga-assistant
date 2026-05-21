/**
 * Tests for the MCP server handlers (called directly — no subprocess).
 * Live Gmail calls are not exercised; only safety / validation paths.
 */

import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

process.env.IGA_EMAIL_MOCK = "1";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
process.env.IGA_RULES_DIR = path.resolve(__dirname, "..", "..", "..", "rules", "email");

const {
  handleTriage,
  handleTrash,
  handleFiltersCreate,
  handleFiltersDelete,
  handleLabelsCreate,
} = await import("../src/mcp-server.js");

function textOf(result: { content: Array<{ type: string; text: string }>; isError?: boolean }): string {
  return result.content.map((c) => c.text).join("\n");
}

describe("mcp-server handlers", () => {
  it("triage with dryRun=true returns a triage report shape", async () => {
    const result = await handleTriage({
      account: ["work"],
      maxResults: 25,
      dryRun: true,
      batchSize: 10,
      runHooks: false,
    });
    assert.equal(result.isError, undefined);
    const payload = JSON.parse(textOf(result)) as {
      accountsScanned: number;
      messagesScanned: number;
      decisions: unknown[];
      dryRun: boolean;
    };
    assert.equal(payload.dryRun, true);
    assert.equal(payload.accountsScanned, 1);
    assert.ok(Array.isArray(payload.decisions));
  });

  it("triage with unknown account returns helpful error listing valid aliases", async () => {
    const result = await handleTriage({ account: ["bogus-alias-xyz"] });
    assert.equal(result.isError, true);
    const msg = textOf(result);
    assert.match(msg, /bogus-alias-xyz/);
    assert.match(msg, /Valid aliases/);
  });

  it("trash without confirm=true returns an error", async () => {
    const result = await handleTrash({
      account: "work",
      messageIds: ["abc123"],
      confirm: false,
    });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /confirm: true/);
  });

  it("filters_delete without confirm=true returns an error", async () => {
    const result = await handleFiltersDelete({
      account: "work",
      ids: ["filt-1"],
      confirm: false,
    });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /confirm: true/);
  });

  it("filters_create with no criteria returns a validation error", async () => {
    const result = await handleFiltersCreate({
      account: "work",
      criteria: {},
      action: { addLabelIds: ["Label_1"] },
    });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /criteria/);
  });

  it("filters_create with no action returns a validation error", async () => {
    const result = await handleFiltersCreate({
      account: "work",
      criteria: { from: "noreply@example.com" },
      action: {},
    });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /action/);
  });

  it("filters_create with unknown account returns helpful error", async () => {
    const result = await handleFiltersCreate({
      account: "not-a-real-alias",
      criteria: { from: "x@y.com" },
      action: { addLabelIds: ["L1"] },
    });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /Valid aliases/);
  });

  it("labels_create with empty name returns an error", async () => {
    const result = await handleLabelsCreate({ account: "work", name: "  " });
    assert.equal(result.isError, true);
    assert.match(textOf(result), /name/);
  });
});

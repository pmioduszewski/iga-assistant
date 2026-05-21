import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  buildPrompt,
  classifyBatched,
  tryParseClassifications,
} from "../src/classifier.js";
import type { GmailMessage, TaxonomyConfig } from "../src/types.js";

const TAXONOMY: TaxonomyConfig = {
  intentLabels: ["Action", "Newsletter/Dev", "Newsletter/Business", "Promo", "Reference"],
  projectLabels: ["Acme", "Iga"],
  inboxStays: new Set(["Action"]),
  autoArchive: new Set(["Newsletter/Dev", "Newsletter/Business", "Promo"]),
  promoDomains: new Set(),
};

function msg(id: string, subject = ""): GmailMessage {
  return {
    id, threadId: id, account: "t",
    from: "A <a@a.com>", fromEmail: "a@a.com", fromDomain: "a.com",
    subject, snippet: "", bodyPreview: "",
    labelIds: [], labelNames: [],
    hasAttachment: false, hasListUnsubscribe: false, internalDate: 0,
  };
}

describe("classifier prompt", () => {
  it("includes message count and label lists", () => {
    const p = buildPrompt([msg("a", "Hello"), msg("b", "World")], TAXONOMY);
    assert.match(p, /MESSAGE 1/);
    assert.match(p, /MESSAGE 2/);
    assert.match(p, /Newsletter\/Dev/);
    assert.match(p, /Acme/);
    assert.match(p, /JSON array/i);
  });

  it("truncates long subjects", () => {
    const long = "x".repeat(500);
    const p = buildPrompt([msg("a", long)], TAXONOMY);
    assert.ok(p.length < 500 + 4000, "prompt should not balloon with raw long subject");
  });
});

describe("JSON parsing", () => {
  it("parses bare JSON array", () => {
    const raw = '[{"message_id":"a","intent_label":"Newsletter/Dev","project_label":"Acme","confidence":0.9,"reason":"dev nl"}]';
    const parsed = tryParseClassifications(raw);
    assert.ok(parsed);
    assert.equal(parsed!.length, 1);
    assert.equal(parsed![0]!.intent_label, "Newsletter/Dev");
  });

  it("strips markdown fences", () => {
    const raw = '```json\n[{"message_id":"a","intent_label":"Action","confidence":0.7,"reason":"x"}]\n```';
    const parsed = tryParseClassifications(raw);
    assert.ok(parsed);
    assert.equal(parsed!.length, 1);
  });

  it("tolerates surrounding prose", () => {
    const raw = 'Here is the result:\n[{"message_id":"x","intent_label":"Promo","confidence":0.6,"reason":"y"}]\nDone.';
    const parsed = tryParseClassifications(raw);
    assert.ok(parsed);
  });

  it("rejects invalid shape", () => {
    const raw = '[{"foo":"bar"}]';
    const parsed = tryParseClassifications(raw);
    assert.equal(parsed, null);
  });

  it("rejects non-array", () => {
    const raw = '{"message_id":"a","intent_label":"Action"}';
    const parsed = tryParseClassifications(raw);
    assert.equal(parsed, null);
  });

  it("nullable project_label accepted", () => {
    const raw = '[{"message_id":"a","intent_label":"Action","project_label":null,"confidence":0.5,"reason":"x"}]';
    const parsed = tryParseClassifications(raw);
    assert.ok(parsed);
    assert.equal(parsed![0]!.project_label, null);
  });
});

describe("classifyBatched", () => {
  it("uses fake classifier when provided (no real claude call)", async () => {
    const messages = [msg("a", "TanStack v2"), msg("b", "LinkedIn digest")];
    const results = await classifyBatched(messages, TAXONOMY, {
      batchSize: 10,
      fake: (batch) => batch.map((m) => ({
        message_id: m.id,
        intent_label: m.subject.includes("TanStack") ? "Newsletter/Dev" : "Promo",
        project_label: m.subject.includes("TanStack") ? "Acme" : null,
        confidence: 0.85,
        reason: "fake",
      })),
    });
    assert.equal(results.length, 2);
    assert.equal(results[0]!.intent_label, "Newsletter/Dev");
    assert.equal(results[0]!.project_label, "Acme");
    assert.equal(results[1]!.intent_label, "Promo");
  });

  it("returns empty array for empty input", async () => {
    const results = await classifyBatched([], TAXONOMY, { batchSize: 10 });
    assert.deepEqual(results, []);
  });

  it("respects batch size by calling fake multiple times", async () => {
    const messages = Array.from({ length: 25 }, (_, i) => msg(`m${i}`));
    let batchCalls = 0;
    const results = await classifyBatched(messages, TAXONOMY, {
      batchSize: 10,
      fake: (batch) => {
        batchCalls++;
        return batch.map((m) => ({
          message_id: m.id, intent_label: "Reference",
          project_label: null, confidence: 0.5, reason: "x",
        }));
      },
    });
    assert.equal(results.length, 25);
    assert.equal(batchCalls, 3, "25 msgs / batch 10 = 3 batches");
  });
});

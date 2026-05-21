/**
 * End-to-end smoke test of the orchestrator using mock fixtures.
 * Verifies the pipeline wires together: config load → unread fetch → pre-filter
 * → LLM-stub → label resolve → (skipped: applyLabels in dry-run).
 *
 * The LLM step is bypassed by clobbering `classifyBatched`'s fake hook — we
 * can't do that from outside without DI, so we just rely on pre-filter rules
 * catching all fixture messages OR they fall to Reference via the fallback
 * path when pre-filter misses and no LLM is wired. To keep the test
 * deterministic, fixtures are designed so most messages hit pre-filter rules
 * directly.
 */

import { describe, it, before } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Wire env BEFORE importing modules that capture it.
process.env.IGA_EMAIL_MOCK = "1";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
process.env.IGA_RULES_DIR = path.resolve(__dirname, "..", "..", "..", "rules", "email");

const { triage } = await import("../src/triage.js");

describe("triage orchestrator (mock mode)", () => {
  it("scans mock accounts and produces decisions for fixtures", async () => {
    const report = await triage({
      accounts: ["work"], // limit to work, contains 4 fixture messages
      maxResults: 25,
      dryRun: true,        // don't actually call applyLabels
      runHooks: false,
      batchSize: 10,
      mock: true,
    });

    assert.equal(report.accountsScanned, 1);
    assert.equal(report.messagesScanned, 4);

    // 3 of 4 fixture msgs should hit pre-filter rules (biurorach, github-sec, github-bot).
    // 1 (TLDR AI newsletter) goes to LLM — without claude wired it falls to "Reference" via fallback.
    assert.ok(report.preFilterHits >= 3, `expected ≥3 pre-filter hits, got ${report.preFilterHits}`);

    // Find specific decisions.
    const biurorach = report.decisions.find((d) => d.message.id === "msg-w-001");
    assert.equal(biurorach?.intent, "Accountant");
    assert.ok(biurorach?.extras.includes("star"));

    const ghSec = report.decisions.find((d) => d.message.id === "msg-w-002");
    assert.equal(ghSec?.intent, "Security");

    const ghBot = report.decisions.find((d) => d.message.id === "msg-w-003");
    assert.equal(ghBot?.intent, "Status");
  });

  it("respects --account filter", async () => {
    const report = await triage({
      accounts: ["umbrella"],
      maxResults: 25,
      dryRun: true,
      runHooks: false,
      batchSize: 10,
      mock: true,
    });
    assert.equal(report.accountsScanned, 1);
    assert.equal(report.messagesScanned, 0);
    assert.equal(report.decisions.length, 0);
  });
});

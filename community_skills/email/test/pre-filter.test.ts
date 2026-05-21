import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { preFilter, isLlmCandidate, DEFAULT_RULES } from "../src/pre-filter.js";
import type { GmailMessage, SenderRule, TaxonomyConfig } from "../src/types.js";

const TAXONOMY: TaxonomyConfig = {
  intentLabels: [
    "Action", "Wait", "Family", "Security", "Receipt",
    "Status", "Newsletter/Dev", "Newsletter/Design",
    "Newsletter/News", "Newsletter/Business",
    "Promo", "Reference", "Domain", "Domain/AfterMarket",
    "Order", "Accountant",
  ],
  projectLabels: ["Acme", "Globex", "Umbrella", "Personal", "Iga"],
  inboxStays: new Set(["Action", "Wait", "Family", "Security", "Receipt", "Accountant"]),
  autoArchive: new Set([
    "Newsletter/Dev", "Newsletter/Design",
    "Newsletter/News", "Newsletter/Business", "Promo",
  ]),
  promoDomains: new Set([
    "linkedin.com", "otomoto.pl", "allegro.pl", "ikea.com", "spotify.com",
    "bolt.eu", "duolingo.com", "myheritage.com", "autoplac.pl", "viltrox.com",
  ]),
};

// Synthetic sender rules — these used to live hardcoded in pre-filter.ts. Now they
// originate from rules/email/accounts.md and are passed in at runtime.
const SENDER_RULES: SenderRule[] = [
  {
    name: "accountant",
    fromDomain: "biurorach.pl",
    intent: "Accountant",
    extraLabels: ["Receipt"],
    star: true,
  },
  {
    name: "jan",
    fromEmailPrefix: "jan.kowalski@",
    intent: "Newsletter/Business",
    extraLabels: [],
    star: false,
  },
  {
    name: "google-security",
    fromEmail: "no-reply@accounts.google.com",
    intent: "Security",
    extraLabels: [],
    star: false,
  },
  {
    name: "github-security-advisory",
    fromEmail: "notifications@github.com",
    subjectKeyword: /security advisory/i,
    intent: "Security",
    extraLabels: [],
    star: false,
  },
  {
    name: "github-bot",
    fromEmail: "*[bot]@github.com",
    intent: "Status",
    extraLabels: [],
    star: false,
  },
  {
    name: "dmarc-google",
    fromEmail: "dmarc-support@google.com",
    intent: "Status",
    extraLabels: [],
    star: false,
  },
  {
    name: "dmarc-generic",
    fromEmailPrefix: "dmarc@",
    intent: "Status",
    extraLabels: [],
    star: false,
  },
];

function msg(overrides: Partial<GmailMessage>): GmailMessage {
  return {
    id: "x",
    threadId: "x",
    account: "test",
    from: "X <x@x.com>",
    fromEmail: "x@x.com",
    fromDomain: "x.com",
    subject: "",
    snippet: "",
    bodyPreview: "",
    labelIds: [],
    labelNames: [],
    hasAttachment: false,
    hasListUnsubscribe: false,
    internalDate: 0,
    ...overrides,
  };
}

describe("pre-filter rules", () => {
  it("accountant biurorach.pl → Accountant + star + Receipt extra", () => {
    const m = msg({
      from: "Biuro <biuro@biurorach.pl>",
      fromEmail: "biuro@biurorach.pl",
      fromDomain: "biurorach.pl",
      subject: "Faktura 123",
    });
    const d = preFilter(m, TAXONOMY, { senderRules: SENDER_RULES });
    assert.ok(d, "should match");
    assert.equal(d!.intent, "Accountant");
    assert.ok(d!.extras.includes("star"));
    assert.ok(d!.extras.includes("Receipt"));
    assert.equal(d!.source, "pre-filter");
    assert.equal(d!.archive, false, "Accountant stays in inbox");
  });

  it("receipt regex → Receipt", () => {
    const d = preFilter(msg({ subject: "Faktura VAT 2026" }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Receipt");
  });

  it("polish security alert subject → Security", () => {
    const d = preFilter(msg({ subject: "Alert bezpieczeństwa" }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Security");
  });

  it("Google security sender → Security", () => {
    const d = preFilter(msg({
      fromEmail: "no-reply@accounts.google.com",
      fromDomain: "accounts.google.com",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Security");
  });

  it("github security advisory → Security", () => {
    const d = preFilter(msg({
      fromEmail: "notifications@github.com",
      fromDomain: "github.com",
      subject: "[security advisory] CVE-2025-9999",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Security");
  });

  it("github bot sender → Status", () => {
    const d = preFilter(msg({
      fromEmail: "dependabot[bot]@github.com",
      fromDomain: "github.com",
      subject: "Bump zod",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Status");
  });

  it("dmarc sender → Status", () => {
    const d = preFilter(msg({
      fromEmail: "dmarc-support@google.com",
      fromDomain: "google.com",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Status");
  });

  it("promo domain → Promo + archive", () => {
    const d = preFilter(msg({
      fromEmail: "spam@linkedin.com",
      fromDomain: "linkedin.com",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Promo");
    assert.equal(d?.archive, true);
  });

  it("jan.kowalski → Newsletter/Business + archive", () => {
    const d = preFilter(msg({
      fromEmail: "jan.kowalski@example.com",
      fromDomain: "example.com",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Newsletter/Business");
    assert.equal(d?.archive, true);
  });

  it("rule precedence: accountant beats receipt regex", () => {
    const d = preFilter(msg({
      from: "Biuro <biuro@biurorach.pl>",
      fromEmail: "biuro@biurorach.pl",
      fromDomain: "biurorach.pl",
      subject: "Faktura VAT 2026",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.intent, "Accountant");
  });

  it("no rule match → returns null (escalate to LLM)", () => {
    const d = preFilter(msg({
      from: "Random <random@example.org>",
      fromEmail: "random@example.org",
      fromDomain: "example.org",
      subject: "Hello, want to chat?",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d, null);
  });

  it("auto-archive applied for Newsletter labels", () => {
    const d = preFilter(msg({
      fromEmail: "jan.kowalski@foo.com",
      fromDomain: "foo.com",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    assert.equal(d?.archive, true);
  });

  it("github advisory AND-keyword required — without subject keyword no match", () => {
    const d = preFilter(msg({
      fromEmail: "notifications@github.com",
      fromDomain: "github.com",
      subject: "Just a PR comment",
    }), TAXONOMY, { senderRules: SENDER_RULES });
    // No senderRule matches (subject doesn't contain "security advisory"); falls through to defaults; no default matches → null.
    assert.equal(d, null);
  });

  it("disabling a default rule via overrides skips it", () => {
    const d = preFilter(msg({ subject: "Faktura 99" }), TAXONOMY, {
      disabledDefaultRules: new Set(["subject-receipt"]),
    });
    assert.equal(d, null);
  });

  it("DEFAULT_RULES contains no user-specific names", () => {
    const names = DEFAULT_RULES.map((r) => r.name).join(",");
    assert.ok(!/biurorach/i.test(names));
    assert.ok(!/jan/i.test(names));
  });
});

describe("LLM candidate gate", () => {
  it("has list-unsubscribe → candidate", () => {
    assert.equal(isLlmCandidate(msg({ hasListUnsubscribe: true })), true);
  });
  it("has subject only → candidate", () => {
    assert.equal(isLlmCandidate(msg({ subject: "hi" })), true);
  });
  it("totally empty → not a candidate", () => {
    assert.equal(isLlmCandidate(msg({})), false);
  });
});

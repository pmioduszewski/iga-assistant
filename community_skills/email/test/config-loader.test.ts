import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { loadAccounts, loadOverrides, parseSenderRules } from "../src/config-loader.js";

async function withTempRulesDir(
  files: Record<string, string>,
  fn: (dir: string) => Promise<void>,
): Promise<void> {
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "iga-rules-"));
  try {
    for (const [name, content] of Object.entries(files)) {
      await fs.writeFile(path.join(tmp, name), content, "utf8");
    }
    await fn(tmp);
  } finally {
    await fs.rm(tmp, { recursive: true, force: true });
  }
}

describe("config-loader sender rules", () => {
  it("parses *@domain pattern", () => {
    const tables = [[
      ["Sender pattern", "Action"],
      ["`*@biurorach.pl`", "`Accountant` + `Receipt` + star (P0)"],
    ]];
    const rules = parseSenderRules(tables);
    assert.equal(rules.length, 1);
    assert.equal(rules[0]!.fromDomain, "biurorach.pl");
    assert.equal(rules[0]!.intent, "Accountant");
    assert.deepEqual(rules[0]!.extraLabels, ["Receipt"]);
    assert.equal(rules[0]!.star, true);
  });

  it("parses name@* prefix pattern", () => {
    const tables = [[
      ["Sender pattern", "Action"],
      ["`jan.kowalski@*`", "`Newsletter/Business` (former contact)"],
    ]];
    const rules = parseSenderRules(tables);
    assert.equal(rules.length, 1);
    assert.equal(rules[0]!.fromEmailPrefix, "jan.kowalski@");
    assert.equal(rules[0]!.intent, "Newsletter/Business");
  });

  it("parses +keyword AND-condition", () => {
    const tables = [[
      ["Sender pattern", "Action"],
      ["`notifications@github.com`+security advisory", "`Security`"],
    ]];
    const rules = parseSenderRules(tables);
    assert.equal(rules.length, 1);
    assert.equal(rules[0]!.fromEmail, "notifications@github.com");
    assert.ok(rules[0]!.subjectKeyword);
    assert.match("a [security advisory] alert", rules[0]!.subjectKeyword!);
  });

  it("parses slash-alternation as multiple rules", () => {
    const tables = [[
      ["Sender pattern", "Action"],
      ["`dmarc-support@google.com` / `dmarc@*`", "`Status`"],
    ]];
    const rules = parseSenderRules(tables);
    assert.equal(rules.length, 2);
    assert.equal(rules[0]!.fromEmail, "dmarc-support@google.com");
    assert.equal(rules[1]!.fromEmailPrefix, "dmarc@");
  });

  it("loadAccounts pulls sender rules table from accounts.md", async () => {
    const md = `# Accounts

| Alias | Address | Notes |
| ----- | ------- | ----- |
| \`work\` | \`me@example.com\` | primary |

## Per-sender hard rules

| Sender pattern | Action |
| -------------- | ------ |
| \`*@biurorach.pl\` | \`Accountant\` + \`Receipt\` + star |
| \`*[bot]@github.com\` | \`Status\` |
`;
    await withTempRulesDir({ "accounts.md": md }, async (dir) => {
      const { accounts, senderRules } = await loadAccounts(dir);
      assert.equal(accounts.length, 1);
      assert.equal(senderRules.length, 2);
      assert.equal(senderRules[0]!.fromDomain, "biurorach.pl");
      assert.equal(senderRules[1]!.fromEmail, "*[bot]@github.com");
    });
  });

  it("loadOverrides parses Disable default rules section", async () => {
    const md = `# Overrides

## Disable default rules

- \`subject-receipt\`
- subject-security
`;
    await withTempRulesDir({ "overrides.md": md }, async (dir) => {
      const { disabledDefaultRules } = await loadOverrides(dir);
      assert.ok(disabledDefaultRules.has("subject-receipt"));
      assert.ok(disabledDefaultRules.has("subject-security"));
    });
  });

  it("loadOverrides returns empty set when file missing", async () => {
    await withTempRulesDir({}, async (dir) => {
      const { disabledDefaultRules } = await loadOverrides(dir);
      assert.equal(disabledDefaultRules.size, 0);
    });
  });
});

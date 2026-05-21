import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  ensureLabelsImpl,
  type CanonicalLabelSpec,
  type GmailLabelSummary,
} from "../src/google/gmail-client.js";
import type { LabelColor } from "../src/google/label-colors.js";

interface Recorder {
  created: Array<{ name: string; color?: LabelColor }>;
  patched: Array<{ id: string; color?: LabelColor; name?: string }>;
}

function mockClient(existing: GmailLabelSummary[]): {
  client: Parameters<typeof ensureLabelsImpl>[0];
  rec: Recorder;
} {
  const rec: Recorder = { created: [], patched: [] };
  const client = {
    async listLabels(): Promise<GmailLabelSummary[]> {
      return existing;
    },
    async createLabel(name: string, color?: LabelColor) {
      rec.created.push({ name, ...(color ? { color } : {}) });
      const id = `Label_new_${rec.created.length}`;
      const out: { id: string; name: string; color?: LabelColor } = { id, name };
      if (color) out.color = color;
      // also append to existing so subsequent canonical entries see it
      existing.push({ id, name, type: "user", ...(color ? { color } : {}) });
      return out;
    },
    async updateLabel(
      labelId: string,
      patch: { name?: string; color?: LabelColor },
    ) {
      rec.patched.push({ id: labelId, ...patch });
      const found = existing.find((l) => l.id === labelId);
      const out: { id: string; name: string; color?: LabelColor } = {
        id: labelId,
        name: patch.name ?? found?.name ?? "",
      };
      if (patch.color) {
        out.color = patch.color;
        if (found) found.color = patch.color;
      }
      return out;
    },
  };
  return { client, rec };
}

const RED: LabelColor = { textColor: "#ffffff", backgroundColor: "#cc3a21" };
const BLUE: LabelColor = { textColor: "#ffffff", backgroundColor: "#4986e7" };

describe("ensureLabels", () => {
  it("creates a missing label with the declared color", async () => {
    const { client, rec } = mockClient([]);
    const canonical: CanonicalLabelSpec[] = [{ name: "Promo", color: RED }];
    const result = await ensureLabelsImpl(client, canonical);

    assert.equal(result.created.length, 1);
    assert.equal(result.created[0]!.name, "Promo");
    assert.deepEqual(result.created[0]!.color, RED);
    assert.equal(result.updated.length, 0);
    assert.equal(result.unchanged.length, 0);
    assert.equal(rec.created.length, 1);
  });

  it("patches a label whose color differs from canonical", async () => {
    const { client, rec } = mockClient([
      { id: "Label_1", name: "Promo", type: "user", color: BLUE },
    ]);
    const canonical: CanonicalLabelSpec[] = [{ name: "Promo", color: RED }];
    const result = await ensureLabelsImpl(client, canonical);

    assert.equal(result.created.length, 0);
    assert.equal(result.updated.length, 1);
    assert.equal(result.unchanged.length, 0);
    assert.deepEqual(result.updated[0]!.before.color, BLUE);
    assert.deepEqual(result.updated[0]!.after.color, RED);
    assert.equal(rec.patched.length, 1);
    assert.equal(rec.patched[0]!.id, "Label_1");
    assert.deepEqual(rec.patched[0]!.color, RED);
  });

  it("leaves matching labels untouched (unchanged)", async () => {
    const { client, rec } = mockClient([
      { id: "Label_1", name: "Promo", type: "user", color: RED },
    ]);
    const result = await ensureLabelsImpl(client, [{ name: "Promo", color: RED }]);
    assert.equal(result.unchanged.length, 1);
    assert.equal(result.created.length, 0);
    assert.equal(result.updated.length, 0);
    assert.equal(rec.created.length, 0);
    assert.equal(rec.patched.length, 0);
  });

  it("never touches labels not in the canonical list", async () => {
    const { client, rec } = mockClient([
      { id: "Label_1", name: "Promo", type: "user", color: BLUE },
      { id: "Label_2", name: "the userOnly", type: "user", color: RED },
      { id: "Label_3", name: "Receipts", type: "user" }, // his manual one, no color
    ]);
    const canonical: CanonicalLabelSpec[] = [
      { name: "Promo", color: RED },
      { name: "Action" }, // missing, no color
    ];
    const result = await ensureLabelsImpl(client, canonical);

    // Promo gets patched, Action gets created, the other two are untouched.
    assert.equal(result.updated.length, 1);
    assert.equal(result.created.length, 1);
    const touched = new Set([
      ...rec.created.map((c) => c.name),
      ...rec.patched.map((p) => p.id),
    ]);
    assert.ok(!touched.has("the userOnly"));
    assert.ok(!touched.has("Label_2"));
    assert.ok(!touched.has("Receipts"));
    assert.ok(!touched.has("Label_3"));
  });

  it("does not patch when canonical declares no color, even if existing has one", async () => {
    const { client, rec } = mockClient([
      { id: "Label_1", name: "Promo", type: "user", color: BLUE },
    ]);
    const result = await ensureLabelsImpl(client, [{ name: "Promo" }]);
    assert.equal(result.unchanged.length, 1);
    assert.deepEqual(result.unchanged[0]!.color, BLUE);
    assert.equal(rec.patched.length, 0);
  });

  it("patches when existing label has no color but canonical declares one", async () => {
    const { client, rec } = mockClient([
      { id: "Label_1", name: "Promo", type: "user" },
    ]);
    const result = await ensureLabelsImpl(client, [{ name: "Promo", color: RED }]);
    assert.equal(result.updated.length, 1);
    assert.equal(result.updated[0]!.before.color, undefined);
    assert.deepEqual(result.updated[0]!.after.color, RED);
    assert.equal(rec.patched.length, 1);
  });
});

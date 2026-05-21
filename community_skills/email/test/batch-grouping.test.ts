import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { groupBatchItems } from "../src/google/types.js";

describe("groupBatchItems", () => {
  it("groups items with identical label sets into a single batch", () => {
    const groups = groupBatchItems([
      { messageId: "m1", addLabelIds: ["A", "B"], removeLabelIds: ["INBOX"] },
      { messageId: "m2", addLabelIds: ["A", "B"], removeLabelIds: ["INBOX"] },
      { messageId: "m3", addLabelIds: ["A", "B"], removeLabelIds: ["INBOX"] },
    ]);
    assert.equal(groups.length, 1);
    assert.deepEqual(groups[0]!.ids, ["m1", "m2", "m3"]);
  });

  it("treats different label orderings as the same group", () => {
    const groups = groupBatchItems([
      { messageId: "m1", addLabelIds: ["A", "B"], removeLabelIds: [] },
      { messageId: "m2", addLabelIds: ["B", "A"], removeLabelIds: [] },
    ]);
    assert.equal(groups.length, 1);
    assert.equal(groups[0]!.ids.length, 2);
  });

  it("splits distinct label sets into separate groups", () => {
    const groups = groupBatchItems([
      { messageId: "m1", addLabelIds: ["A"], removeLabelIds: [] },
      { messageId: "m2", addLabelIds: ["B"], removeLabelIds: [] },
      { messageId: "m3", addLabelIds: ["A"], removeLabelIds: ["INBOX"] },
    ]);
    assert.equal(groups.length, 3);
  });

  it("handles empty input", () => {
    assert.deepEqual(groupBatchItems([]), []);
  });

  it("preserves sorted label ordering in output", () => {
    const groups = groupBatchItems([
      { messageId: "m1", addLabelIds: ["Z", "A", "M"], removeLabelIds: [] },
    ]);
    assert.deepEqual(groups[0]!.addLabelIds, ["A", "M", "Z"]);
  });
});

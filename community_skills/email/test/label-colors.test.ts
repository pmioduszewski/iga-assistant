import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  ALLOWED_LABEL_COLORS,
  COLOR_ALIASES,
  colorEquals,
  isAllowedColor,
  isNamedAlias,
  resolveColor,
} from "../src/google/label-colors.js";

describe("label-colors palette", () => {
  it("ALLOWED_LABEL_COLORS contains at least 24 distinct pairs", () => {
    assert.ok(ALLOWED_LABEL_COLORS.length >= 24);
    const keys = new Set(ALLOWED_LABEL_COLORS.map((c) => `${c.textColor}|${c.backgroundColor}`));
    assert.equal(keys.size, ALLOWED_LABEL_COLORS.length, "no duplicate pairs");
  });

  it("every named alias resolves to an allowed pair", () => {
    for (const [name, color] of Object.entries(COLOR_ALIASES)) {
      assert.ok(
        isAllowedColor(color.textColor, color.backgroundColor),
        `alias "${name}" -> ${color.textColor}/${color.backgroundColor} must be in palette`,
      );
    }
  });

  it("includes the expected core aliases", () => {
    for (const name of ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "gray"]) {
      assert.ok(isNamedAlias(name), `expected alias: ${name}`);
    }
  });
});

describe("resolveColor", () => {
  it("resolves a named alias (case-insensitive)", () => {
    const a = resolveColor("red");
    const b = resolveColor("RED");
    assert.deepEqual(a, b);
    assert.equal(a.backgroundColor, "#cc3a21");
  });

  it("resolves dark/light variants", () => {
    const dark = resolveColor("red-dark");
    const light = resolveColor("red-light");
    assert.notDeepEqual(dark, light);
    assert.ok(isAllowedColor(dark.textColor, dark.backgroundColor));
    assert.ok(isAllowedColor(light.textColor, light.backgroundColor));
  });

  it("resolves slash hex form #text/#bg", () => {
    const c = resolveColor("#ffffff/#cc3a21");
    assert.equal(c.textColor, "#ffffff");
    assert.equal(c.backgroundColor, "#cc3a21");
  });

  it("resolves explicit pair object", () => {
    const c = resolveColor({ textColor: "#ffffff", backgroundColor: "#cc3a21" });
    assert.equal(c.backgroundColor, "#cc3a21");
  });

  it("throws on unknown alias", () => {
    assert.throws(() => resolveColor("turquoise-supreme"), /unknown alias/i);
  });

  it("throws on pair not in palette", () => {
    assert.throws(
      () => resolveColor("#123456/#abcdef"),
      /not in Gmail's allowed palette/i,
    );
  });

  it("throws on malformed hex", () => {
    assert.throws(() => resolveColor("#zzz/#aaaaaa"), /invalid hex/i);
  });

  it("throws on empty string", () => {
    assert.throws(() => resolveColor(""), /empty input/i);
  });
});

describe("colorEquals", () => {
  it("is case-insensitive and undefined-tolerant", () => {
    assert.equal(
      colorEquals(
        { textColor: "#FFFFFF", backgroundColor: "#CC3A21" },
        { textColor: "#ffffff", backgroundColor: "#cc3a21" },
      ),
      true,
    );
    assert.equal(colorEquals(undefined, undefined), true);
    assert.equal(colorEquals({ textColor: "#fff", backgroundColor: "#000" }, undefined), false);
  });
});

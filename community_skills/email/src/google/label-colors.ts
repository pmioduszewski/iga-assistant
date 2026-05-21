/**
 * Gmail allowed label-color palette and resolver.
 *
 * Gmail's `users.labels` API rejects arbitrary hex pairs — only the fixed
 * palette below is accepted. Source of truth:
 *   https://developers.google.com/gmail/api/reference/rest/v1/users.labels#color
 *
 * We also expose named aliases ("red", "blue", ...) plus -dark/-light variants
 * so taxonomy.md authors don't have to memorize hex pairs.
 */

export interface LabelColor {
  textColor: string;
  backgroundColor: string;
}

/**
 * Full set of (textColor, backgroundColor) pairs that Gmail accepts.
 * Verified against the Gmail API reference (see file header).
 */
export const ALLOWED_LABEL_COLORS: ReadonlyArray<LabelColor> = [
  // Greys / black / white
  { textColor: "#000000", backgroundColor: "#ffffff" },
  { textColor: "#ffffff", backgroundColor: "#000000" },
  { textColor: "#434343", backgroundColor: "#efefef" },
  { textColor: "#666666", backgroundColor: "#f3f3f3" },
  { textColor: "#ffffff", backgroundColor: "#434343" },
  { textColor: "#ffffff", backgroundColor: "#666666" },
  // Reds
  { textColor: "#ffffff", backgroundColor: "#cc3a21" }, // red
  { textColor: "#594c05", backgroundColor: "#fb4c2f" }, // red-dark (bright bg)
  { textColor: "#ffffff", backgroundColor: "#ac2b16" }, // red-deep
  { textColor: "#cc3a21", backgroundColor: "#fbd3e0" }, // red-light
  // Oranges
  { textColor: "#ffffff", backgroundColor: "#ff7537" }, // orange
  { textColor: "#7a2e0b", backgroundColor: "#ffad47" }, // orange-light
  { textColor: "#a46a21", backgroundColor: "#ffe6c7" }, // orange-pale
  // Yellows
  { textColor: "#684e07", backgroundColor: "#fad165" }, // yellow
  { textColor: "#594c05", backgroundColor: "#fcda83" }, // yellow-soft
  { textColor: "#684e07", backgroundColor: "#fef1d1" }, // yellow-light
  // Greens
  { textColor: "#ffffff", backgroundColor: "#149e60" }, // green
  { textColor: "#04502e", backgroundColor: "#a2dcc1" }, // green-light
  { textColor: "#0b804b", backgroundColor: "#c6f3de" }, // green-pale
  { textColor: "#ffffff", backgroundColor: "#16a766" }, // green-bright
  // Teals
  { textColor: "#ffffff", backgroundColor: "#43d692" }, // teal
  { textColor: "#04502e", backgroundColor: "#68dfa9" }, // teal-light
  // Blues
  { textColor: "#ffffff", backgroundColor: "#4986e7" }, // blue
  { textColor: "#0d3472", backgroundColor: "#a4c2f4" }, // blue-light
  { textColor: "#ffffff", backgroundColor: "#3c78d8" }, // blue-deep
  { textColor: "#1c4587", backgroundColor: "#c9daf8" }, // blue-pale
  // Purples
  { textColor: "#ffffff", backgroundColor: "#8e63ce" }, // purple
  { textColor: "#3d188e", backgroundColor: "#b694e8" }, // purple-light
  { textColor: "#41236d", backgroundColor: "#e4d7f5" }, // purple-pale
  // Pinks / magentas
  { textColor: "#ffffff", backgroundColor: "#e07798" }, // pink
  { textColor: "#711a36", backgroundColor: "#fbc8d9" }, // pink-light
];

/** Lookup-key for a color pair (lower-cased hex). */
function key(text: string, bg: string): string {
  return `${text.toLowerCase()}|${bg.toLowerCase()}`;
}

const ALLOWED_KEYS = new Set<string>(
  ALLOWED_LABEL_COLORS.map((c) => key(c.textColor, c.backgroundColor)),
);

/** Named aliases — case-insensitive. */
export const COLOR_ALIASES: Readonly<Record<string, LabelColor>> = {
  black: { textColor: "#ffffff", backgroundColor: "#000000" },
  white: { textColor: "#000000", backgroundColor: "#ffffff" },
  gray: { textColor: "#ffffff", backgroundColor: "#666666" },
  grey: { textColor: "#ffffff", backgroundColor: "#666666" },
  "gray-light": { textColor: "#434343", backgroundColor: "#efefef" },
  "gray-dark": { textColor: "#ffffff", backgroundColor: "#434343" },

  red: { textColor: "#ffffff", backgroundColor: "#cc3a21" },
  "red-dark": { textColor: "#ffffff", backgroundColor: "#ac2b16" },
  "red-light": { textColor: "#cc3a21", backgroundColor: "#fbd3e0" },

  orange: { textColor: "#ffffff", backgroundColor: "#ff7537" },
  "orange-light": { textColor: "#7a2e0b", backgroundColor: "#ffad47" },
  "orange-pale": { textColor: "#a46a21", backgroundColor: "#ffe6c7" },

  yellow: { textColor: "#684e07", backgroundColor: "#fad165" },
  "yellow-light": { textColor: "#684e07", backgroundColor: "#fef1d1" },

  green: { textColor: "#ffffff", backgroundColor: "#149e60" },
  "green-dark": { textColor: "#ffffff", backgroundColor: "#16a766" },
  "green-light": { textColor: "#04502e", backgroundColor: "#a2dcc1" },

  teal: { textColor: "#ffffff", backgroundColor: "#43d692" },
  "teal-light": { textColor: "#04502e", backgroundColor: "#68dfa9" },

  blue: { textColor: "#ffffff", backgroundColor: "#4986e7" },
  "blue-dark": { textColor: "#ffffff", backgroundColor: "#3c78d8" },
  "blue-light": { textColor: "#0d3472", backgroundColor: "#a4c2f4" },
  "blue-pale": { textColor: "#1c4587", backgroundColor: "#c9daf8" },

  purple: { textColor: "#ffffff", backgroundColor: "#8e63ce" },
  "purple-light": { textColor: "#3d188e", backgroundColor: "#b694e8" },
  "purple-pale": { textColor: "#41236d", backgroundColor: "#e4d7f5" },

  pink: { textColor: "#ffffff", backgroundColor: "#e07798" },
  "pink-light": { textColor: "#711a36", backgroundColor: "#fbc8d9" },
};

export function isAllowedColor(textColor: string, backgroundColor: string): boolean {
  return ALLOWED_KEYS.has(key(textColor, backgroundColor));
}

export function isNamedAlias(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(COLOR_ALIASES, name.trim().toLowerCase());
}

/**
 * Resolve a user-supplied color spec into a Gmail-accepted pair.
 *
 * Accepted forms:
 *   - Named alias: "red", "blue-light", "Pink-Light" (case-insensitive)
 *   - Explicit pair: `{ textColor: "#fff", backgroundColor: "#cc3a21" }`
 *   - Slash hex string: `"#ffffff/#cc3a21"` (textColor/backgroundColor)
 *
 * Throws if the result isn't in {@link ALLOWED_LABEL_COLORS}.
 */
export function resolveColor(
  input: string | LabelColor,
): LabelColor {
  if (typeof input === "string") {
    const trimmed = input.trim();
    if (!trimmed) throw new Error("resolveColor: empty input");

    // Slash form: "#text/#bg"
    if (trimmed.includes("/")) {
      const [t, b] = trimmed.split("/").map((s) => s.trim());
      if (!t || !b) throw new Error(`resolveColor: malformed slash pair "${input}"`);
      const candidate: LabelColor = {
        textColor: normalizeHex(t),
        backgroundColor: normalizeHex(b),
      };
      if (!isAllowedColor(candidate.textColor, candidate.backgroundColor)) {
        throw new Error(
          `resolveColor: pair ${candidate.textColor}/${candidate.backgroundColor} is not in Gmail's allowed palette`,
        );
      }
      return candidate;
    }

    const alias = COLOR_ALIASES[trimmed.toLowerCase()];
    if (!alias) {
      throw new Error(
        `resolveColor: unknown alias "${input}". Known: ${Object.keys(COLOR_ALIASES).sort().join(", ")}`,
      );
    }
    return alias;
  }

  const normalized: LabelColor = {
    textColor: normalizeHex(input.textColor),
    backgroundColor: normalizeHex(input.backgroundColor),
  };
  if (!isAllowedColor(normalized.textColor, normalized.backgroundColor)) {
    throw new Error(
      `resolveColor: pair ${normalized.textColor}/${normalized.backgroundColor} is not in Gmail's allowed palette`,
    );
  }
  return normalized;
}

function normalizeHex(s: string): string {
  const t = s.trim().toLowerCase();
  if (!/^#[0-9a-f]{6}$/.test(t)) {
    throw new Error(`resolveColor: invalid hex "${s}" (expected #rrggbb)`);
  }
  return t;
}

/** Equality helper, tolerant of hex case. */
export function colorEquals(a: LabelColor | undefined, b: LabelColor | undefined): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return (
    a.textColor.toLowerCase() === b.textColor.toLowerCase() &&
    a.backgroundColor.toLowerCase() === b.backgroundColor.toLowerCase()
  );
}

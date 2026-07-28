import type { FoundationKey } from "./defaultTokens";
import { hexToRgba, shiftColor } from "./colorUtils";

export interface VerseTokenDef {
  id: string;
  name: string;
  cat: "syntax" | "chrome";
  /** Foundation key used when showing AUTO linkage in the UI. */
  base: FoundationKey;
  rule: (vars: Record<string, string>, foundation: Record<FoundationKey, string>) => string;
  /** rgba tokens get a color + opacity control in Appearance (stored as rgba()). */
  colorKind?: "hex" | "rgba";
}

function v(key: string, vars: Record<string, string>, fallback: string): string {
  return vars[key] || fallback;
}

export const VERSE_SYNTAX_CATEGORIES = [
  { id: "syntax", name: "Syntax tokens" },
  { id: "chrome", name: "Editor chrome" },
] as const;

/** Verse Monaco syntax + editor chrome — derived from theme tokens after foundation compute. */
export const VERSE_COLOR_TOKENS: VerseTokenDef[] = [
  {
    id: "verse-syntax-comment",
    name: "Comments",
    cat: "syntax",
    base: "text",
    rule: (vars) => shiftColor(v("green", vars, "#3ddc84"), 0, -18, -18),
  },
  {
    id: "verse-syntax-keyword",
    name: "Keywords",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("accent", vars, "#2563eb"), 58, 28, 12),
  },
  {
    id: "verse-syntax-type",
    name: "Types",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("accent", vars, "#2563eb"), -48, 22, 6),
  },
  {
    id: "verse-syntax-type-id",
    name: "Type names",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("accent", vars, "#2563eb"), -48, 22, 6),
  },
  {
    id: "verse-syntax-tag",
    name: "Attributes (@)",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), 0, -8, 22),
  },
  {
    id: "verse-syntax-identifier",
    name: "Identifiers",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("blue", vars, "#5eb3f6"), 0, -6, 18),
  },
  {
    id: "verse-syntax-path",
    name: "Module paths",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("blue", vars, "#5eb3f6"), -26, -16, 4),
  },
  {
    id: "verse-syntax-string",
    name: "Strings",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), 18, -18, 2),
  },
  {
    id: "verse-syntax-string-char",
    name: "Char literals",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), -18, 6, 6),
  },
  {
    id: "verse-syntax-string-escape",
    name: "String escapes",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), 8, -5, 26),
  },
  {
    id: "verse-syntax-error",
    name: "Errors",
    cat: "syntax",
    base: "accent",
    rule: (vars) => v("red", vars, "#ff6058"),
  },
  {
    id: "verse-syntax-number",
    name: "Numbers",
    cat: "syntax",
    base: "text",
    rule: (vars) => shiftColor(v("green", vars, "#3ddc84"), 0, -28, 18),
  },
  {
    id: "verse-syntax-operator",
    name: "Operators",
    cat: "syntax",
    base: "text",
    rule: (vars) => v("fg-dim", vars, "#a1a1a1"),
  },
  {
    id: "verse-syntax-operator-arith",
    name: "Arithmetic operators",
    cat: "syntax",
    base: "text",
    rule: (vars) => v("fg-dim", vars, "#a1a1a1"),
  },
  {
    id: "verse-syntax-operator-compare",
    name: "Comparison operators",
    cat: "syntax",
    base: "text",
    rule: (vars) => v("fg-dim", vars, "#a1a1a1"),
  },
  {
    id: "verse-syntax-operator-logical",
    name: "Logical operators (and/or/not)",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("accent", vars, "#2563eb"), 58, 28, 12),
  },
  {
    id: "verse-syntax-bracket",
    name: "Brackets",
    cat: "syntax",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), -8, 12, 32),
  },
  {
    id: "verse-editor-bg",
    name: "Background",
    cat: "chrome",
    base: "bg",
    rule: (vars, foundation) => v("bg", vars, foundation.bg),
  },
  {
    id: "verse-editor-fg",
    name: "Foreground",
    cat: "chrome",
    base: "text",
    rule: (vars) => v("fg", vars, "#ededed"),
  },
  {
    id: "verse-editor-gutter",
    name: "Gutter",
    cat: "chrome",
    base: "bg",
    rule: (vars, foundation) => v("bg", vars, foundation.bg),
  },
  {
    id: "verse-editor-line-number",
    name: "Line numbers",
    cat: "chrome",
    base: "text",
    rule: (vars) => v("muted", vars, "#666666"),
  },
  {
    id: "verse-editor-line-number-active",
    name: "Active line number",
    cat: "chrome",
    base: "text",
    rule: (vars) => v("fg-dim", vars, "#a1a1a1"),
  },
  {
    id: "verse-editor-cursor",
    name: "Cursor",
    cat: "chrome",
    base: "accent",
    rule: (vars) => shiftColor(v("amber", vars, "#e8a317"), 0, 12, 8),
  },
  {
    id: "verse-editor-selection",
    name: "Selection",
    cat: "chrome",
    base: "accent",
    colorKind: "rgba",
    rule: (vars) => hexToRgba(v("accent", vars, "#2563eb"), 0.35),
  },
  {
    id: "verse-editor-selection-inactive",
    name: "Inactive selection",
    cat: "chrome",
    base: "accent",
    colorKind: "rgba",
    rule: (vars) => hexToRgba(v("accent", vars, "#2563eb"), 0.2),
  },
  {
    id: "verse-editor-selection-highlight",
    name: "Matching selection",
    cat: "chrome",
    base: "accent",
    colorKind: "rgba",
    rule: (vars) => hexToRgba(v("accent", vars, "#2563eb"), 0.22),
  },
  {
    id: "verse-editor-line-highlight",
    name: "Current line",
    cat: "chrome",
    base: "surface",
    rule: (vars) => shiftColor(v("card", vars, "#181818"), 0, 0, 8),
  },
  {
    id: "verse-editor-line-highlight-border",
    name: "Current line border",
    cat: "chrome",
    base: "surface",
    rule: (vars) => shiftColor(v("card", vars, "#181818"), 0, 0, 14),
  },
  {
    id: "verse-editor-indent",
    name: "Indent guides",
    cat: "chrome",
    base: "border",
    rule: (vars) => v("border", vars, "#262626"),
  },
  {
    id: "verse-editor-indent-active",
    name: "Active indent guide",
    cat: "chrome",
    base: "border",
    rule: (vars) => v("border-light", vars, "#333333"),
  },
  {
    id: "verse-editor-widget-bg",
    name: "Widget background",
    cat: "chrome",
    base: "surface",
    rule: (vars) => v("card", vars, "#181818"),
  },
  {
    id: "verse-editor-widget-border",
    name: "Widget border",
    cat: "chrome",
    base: "border",
    rule: (vars) => v("border", vars, "#262626"),
  },
  {
    id: "verse-editor-bracket-match-bg",
    name: "Bracket match",
    cat: "chrome",
    base: "accent",
    colorKind: "rgba",
    rule: (vars) => hexToRgba(v("green", vars, "#3ddc84"), 0.12),
  },
  {
    id: "verse-editor-bracket-match-border",
    name: "Bracket match border",
    cat: "chrome",
    base: "text",
    rule: (vars) => v("fg-dim", vars, "#a1a1a1"),
  },
];

export const VERSE_TOKEN_IDS = VERSE_COLOR_TOKENS.map((t) => t.id);

const SYNTAX_TOKEN_PREFIX = "verse-syntax-";

/** Syntax colors derived from base theme vars — ignores verse-syntax appearance overrides. */
export function resolveVerseSyntaxColors(
  vars: Record<string, string>,
  foundation: Record<FoundationKey, string>,
): Record<string, string> {
  const baseVars = { ...vars };
  for (const key of Object.keys(baseVars)) {
    if (key.startsWith(SYNTAX_TOKEN_PREFIX)) {
      delete baseVars[key];
    }
  }
  const out: Record<string, string> = {};
  for (const token of VERSE_COLOR_TOKENS) {
    if (token.cat === "syntax") {
      out[token.id] = token.rule(baseVars, foundation);
    }
  }
  return out;
}

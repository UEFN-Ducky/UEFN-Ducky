import type { FoundationKey } from "./defaultTokens";
import { DEFAULT_CSS_VARS, defaultFoundation } from "./defaultTokens";
import { colorToHexAndAlpha, formatColorWithAlpha, hexToHSL, hexToRgba, parseRgba, shiftColor } from "./colorUtils";
import { applyFontLibraryToCssVars } from "./fontLibrary";
import { VERSE_COLOR_TOKENS, VERSE_TOKEN_IDS } from "./verseSyntaxTokens";

export interface TokenDef {
  id: string;
  name: string;
  cat: string;
  base: FoundationKey;
  rule: (color: string) => string;
  color: boolean;
}

export interface StatusColorDef {
  id: string;
  name: string;
  vars: { bg: string; border: string; text: string; dim: string };
}

export const TOKEN_CATEGORIES = [
  { id: "brand", name: "Brand & Interactive" },
  { id: "surface", name: "Surfaces" },
  { id: "textborder", name: "Typography & Borders" },
  { id: "semantic", name: "Status & Semantic" },
  { id: "layout", name: "Layout & Effects" },
] as const;

const SEMANTIC_COLOR_IDS = ["green", "red", "amber", "blue"] as const;

export const COLOR_TOKENS: TokenDef[] = [
  { id: "accent", name: "Accent", cat: "brand", base: "accent", rule: (c) => c, color: true },
  {
    id: "accent-hover",
    name: "Accent Hover",
    cat: "brand",
    base: "accent",
    rule: (c) => shiftColor(c, 0, 0, -10),
    color: true,
  },
  {
    id: "select",
    name: "Selection",
    cat: "brand",
    base: "accent",
    rule: (c) => shiftColor(c, 0, -15, -15),
    color: true,
  },
  {
    id: "border-focus",
    name: "Focus Ring",
    cat: "brand",
    base: "accent",
    rule: (c) => c,
    color: true,
  },
  { id: "bg", name: "Background", cat: "surface", base: "bg", rule: (c) => c, color: true },
  {
    id: "sidebar",
    name: "Sidebar",
    cat: "surface",
    base: "bg",
    rule: (c) => shiftColor(c, 0, 0, 2),
    color: true,
  },
  {
    id: "panel-header",
    name: "Panel Header",
    cat: "surface",
    base: "bg",
    rule: (c) => shiftColor(c, 0, 0, 4),
    color: true,
  },
  { id: "card", name: "Card", cat: "surface", base: "surface", rule: (c) => c, color: true },
  {
    id: "card-hover",
    name: "Card Hover",
    cat: "surface",
    base: "surface",
    rule: (c) => shiftColor(c, 0, 0, 8),
    color: true,
  },
  {
    id: "bg-elevated",
    name: "Elevated Surface",
    cat: "surface",
    base: "surface",
    rule: (c) => shiftColor(c, 0, 0, 6),
    color: true,
  },
  {
    id: "dropdown-bg",
    name: "Dropdown Bg",
    cat: "surface",
    base: "surface",
    rule: (c) => c,
    color: true,
  },
  {
    id: "input-bg",
    name: "Input Bg",
    cat: "surface",
    base: "surface",
    rule: (c) => hexToRgba(c, 0.6),
    color: true,
  },
  {
    id: "bg-panel",
    name: "Glass Panel",
    cat: "surface",
    base: "surface",
    rule: (c) => hexToRgba(c, 0.65),
    color: true,
  },
  {
    id: "header",
    name: "Header",
    cat: "surface",
    base: "bg",
    rule: (c) => hexToRgba(c, 0.85),
    color: true,
  },
  {
    id: "btn-bg",
    name: "Button Bg",
    cat: "surface",
    base: "surface",
    rule: (c) => shiftColor(c, 0, 0, 12),
    color: true,
  },
  {
    id: "btn-hover",
    name: "Button Hover",
    cat: "surface",
    base: "surface",
    rule: (c) => shiftColor(c, 0, 0, 18),
    color: true,
  },
  {
    id: "btn-pressed",
    name: "Button Pressed",
    cat: "surface",
    base: "surface",
    rule: (c) => shiftColor(c, 0, 0, -4),
    color: true,
  },
  {
    id: "tab-active",
    name: "Tab Active",
    cat: "surface",
    base: "text",
    rule: (c) => hexToRgba(c, 0.08),
    color: true,
  },
  {
    id: "tab-hover",
    name: "Tab Hover",
    cat: "surface",
    base: "text",
    rule: (c) => hexToRgba(c, 0.04),
    color: true,
  },
  {
    id: "overlay",
    name: "Overlay",
    cat: "surface",
    base: "bg",
    rule: (c) => hexToRgba(c, 0.55),
    color: true,
  },
  {
    id: "overlay-light",
    name: "Overlay Light",
    cat: "surface",
    base: "bg",
    rule: (c) => hexToRgba(c, 0.35),
    color: true,
  },
  { id: "fg", name: "Text", cat: "textborder", base: "text", rule: (c) => c, color: true },
  {
    id: "fg-dim",
    name: "Dim Text",
    cat: "textborder",
    base: "text",
    rule: (c) => shiftColor(c, 0, -20, -30),
    color: true,
  },
  {
    id: "fg-inverse",
    name: "Inverse Text",
    cat: "textborder",
    base: "text",
    rule: (c) => c,
    color: true,
  },
  {
    id: "muted",
    name: "Muted Text",
    cat: "textborder",
    base: "text",
    rule: (c) => shiftColor(c, 0, -25, -45),
    color: true,
  },
  { id: "border", name: "Border", cat: "textborder", base: "border", rule: (c) => c, color: true },
  {
    id: "border-light",
    name: "Border Light",
    cat: "textborder",
    base: "border",
    rule: (c) => shiftColor(c, 0, 0, 8),
    color: true,
  },
  {
    id: "on-green",
    name: "On Green",
    cat: "semantic",
    base: "bg",
    rule: (c) => shiftColor(c, 0, 5, 8),
    color: true,
  },
  {
    id: "on-amber",
    name: "On Amber",
    cat: "semantic",
    base: "bg",
    rule: (c) => c,
    color: true,
  },
];

export const TERMINAL_COLOR_TOKENS: TokenDef[] = [
  { id: "terminal-bg", name: "Background", cat: "terminal", base: "bg", rule: (c) => c, color: true },
  { id: "terminal-fg", name: "Text", cat: "terminal", base: "text", rule: (c) => c, color: true },
  { id: "terminal-cursor", name: "Cursor", cat: "terminal", base: "text", rule: (c) => c, color: true },
  {
    id: "terminal-selection",
    name: "Selection",
    cat: "terminal",
    base: "accent",
    rule: (c) => shiftColor(c, 0, -15, -15),
    color: true,
  },
];

export const GROUPCHAT_COLOR_TOKENS: TokenDef[] = [
  // Discord's blurple by default, themeable like everything else.
  { id: "groupchat-accent", name: "Accent", cat: "groupchat", base: "accent", rule: () => "#5865f2", color: true },
  { id: "groupchat-bg", name: "Chat Background", cat: "groupchat", base: "bg", rule: (c) => c, color: true },
  {
    id: "groupchat-hover",
    name: "Message Hover",
    cat: "groupchat",
    base: "text",
    rule: (c) => hexToRgba(c, 0.05),
    color: true,
  },
  {
    id: "groupchat-time",
    name: "Timestamps & Meta",
    cat: "groupchat",
    base: "text",
    rule: (c) => shiftColor(c, 0, -25, -40),
    color: true,
  },
  {
    id: "groupchat-composer-bg",
    name: "Composer Field",
    cat: "groupchat",
    base: "surface",
    rule: (c) => hexToRgba(c, 0.72),
    color: true,
  },
];

/** All editable color tokens (UI + terminal + Discord chat). */
export const ALL_COLOR_TOKEN_DEFS: TokenDef[] = [
  ...COLOR_TOKENS,
  ...TERMINAL_COLOR_TOKENS,
  ...GROUPCHAT_COLOR_TOKENS,
];

export const LAYOUT_TOKENS: TokenDef[] = [
  { id: "radius", name: "Radius", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "radius-sm", name: "Radius Small", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "font-ui", name: "UI Font", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "font-mono", name: "Mono Font", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "shadow-sm", name: "Shadow Small", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "shadow-md", name: "Shadow Medium", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "shadow-lg", name: "Shadow Large", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "shadow-glow", name: "Shadow Glow", cat: "layout", base: "accent", rule: (c) => c, color: false },
  { id: "shadow-dropdown", name: "Shadow Dropdown", cat: "layout", base: "bg", rule: (c) => c, color: false },
  { id: "blur-md", name: "Blur", cat: "layout", base: "bg", rule: (c) => c, color: false },
];

export const STATUS_COLORS: StatusColorDef[] = [
  {
    id: "error",
    name: "Error",
    vars: { bg: "red-dim", border: "red", text: "red", dim: "red-dim" },
  },
  {
    id: "success",
    name: "Success",
    vars: { bg: "green-dim", border: "green", text: "green", dim: "green-dim" },
  },
  {
    id: "info",
    name: "Info",
    vars: { bg: "blue-dim", border: "blue", text: "blue", dim: "blue-dim" },
  },
  {
    id: "warn",
    name: "Warning",
    vars: { bg: "amber-dim", border: "amber", text: "amber", dim: "amber-dim" },
  },
];

export const ALL_TOKEN_IDS = [
  ...ALL_COLOR_TOKEN_DEFS.map((t) => t.id),
  ...LAYOUT_TOKENS.map((t) => t.id),
  ...SEMANTIC_COLOR_IDS,
  ...SEMANTIC_COLOR_IDS.map((c) => `${c}-dim`),
  ...STATUS_COLORS.flatMap((s) => [s.vars.bg, s.vars.border, s.vars.text, s.vars.dim]),
  ...VERSE_TOKEN_IDS,
];

export type AppearanceState = {
  foundation: Record<FoundationKey, string>;
  overrides: Record<string, string>;
  statusOverrides: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
};

function foundationColor(foundation: Record<FoundationKey, string>, key: FoundationKey): string {
  return foundation[key] ?? defaultFoundation()[key];
}

function foundationHex(foundation: Record<FoundationKey, string>, key: FoundationKey): string {
  return colorToHexAndAlpha(foundationColor(foundation, key)).hex;
}

/** Re-apply foundation alpha when token rules output opaque hex or fixed-opacity rgba. */
function applyBaseAlpha(
  computed: string,
  foundation: Record<FoundationKey, string>,
  baseKey: FoundationKey,
): string {
  const { alphaPct } = colorToHexAndAlpha(foundationColor(foundation, baseKey));
  if (alphaPct >= 100) return computed;

  const rgba = parseRgba(computed);
  if (rgba) {
    const a = (rgba.a * alphaPct) / 100;
    return `rgba(${rgba.r}, ${rgba.g}, ${rgba.b}, ${a})`;
  }
  if (/^#[0-9a-fA-F]{6}$/i.test(computed)) {
    return formatColorWithAlpha(computed, alphaPct);
  }
  return computed;
}

function shadowFromColor(color: string, alpha: number): string {
  return hexToRgba(colorToHexAndAlpha(color).hex, alpha);
}

function applySemanticColors(
  vars: Record<string, string>,
  overrides: Record<string, string>,
): void {
  for (const id of SEMANTIC_COLOR_IDS) {
    vars[id] = overrides[id] ?? DEFAULT_CSS_VARS[id];
    const dimId = `${id}-dim`;
    vars[dimId] = overrides[dimId] ?? hexToRgba(vars[id]!, 0.15);
  }
}

function applyShadows(vars: Record<string, string>, overrides: Record<string, string>): void {
  const bg = vars.bg ?? DEFAULT_CSS_VARS.bg;
  const accent = vars.accent ?? DEFAULT_CSS_VARS.accent;
  const shadowDefs: Record<string, string> = {
    "shadow-sm": `0 1px 2px ${shadowFromColor(bg, 0.5)}`,
    "shadow-md": `0 8px 16px ${shadowFromColor(bg, 0.6)}`,
    "shadow-lg": `0 12px 40px ${shadowFromColor(bg, 0.45)}`,
    "shadow-glow": `0 0 15px ${shadowFromColor(accent, 0.15)}`,
    "shadow-dropdown": `0 10px 30px ${shadowFromColor(bg, 0.9)}`,
  };
  for (const [id, value] of Object.entries(shadowDefs)) {
    if (!overrides[id]) vars[id] = value;
  }
}

function applyAliases(vars: Record<string, string>, overrides: Record<string, string>): void {
  const primaryIds = new Set(ALL_COLOR_TOKEN_DEFS.map((t) => t.id));
  const alias = (key: string, from: string) => {
    if (primaryIds.has(key) || overrides[key]) return;
    vars[key] = vars[from]!;
  };

  alias("bg-hover", "card-hover");
  alias("text-muted", "muted");
  alias("text-primary", "fg");
  if (!overrides.text) vars.text = vars.fg!;
  alias("border-subtle", "border");
  alias("danger", "red");
  alias("warn", "amber");
  alias("warn-dim", "amber-dim");

  alias("terminal-bg", "bg");
  alias("terminal-fg", "fg");
  alias("terminal-cursor", "fg");
  alias("terminal-selection", "select");
  alias("terminal-ansi-green", "green");
  alias("terminal-ansi-yellow", "amber");
  alias("terminal-ansi-green-dim", "fg-dim");
  alias("terminal-ansi-red", "red");
  alias("terminal-ansi-fg", "fg");
}

function applyOnSemantic(vars: Record<string, string>, overrides: Record<string, string>): void {
  if (!overrides["on-green"]) {
    vars["on-green"] = shiftColor(vars.green ?? DEFAULT_CSS_VARS.green, 0, -40, -30);
  }
  if (!overrides["on-amber"]) {
    vars["on-amber"] = vars.bg ?? DEFAULT_CSS_VARS.bg;
  }
}

function applyRegionalFonts(vars: Record<string, string>, overrides: Record<string, string>): void {
  const ui = vars["font-ui"]!;
  const regional: Array<{ id: string; fallback: string }> = [
    { id: "font-header", fallback: ui },
    { id: "font-sidebar", fallback: ui },
    { id: "chat-text-font", fallback: ui },
    { id: "font-settings", fallback: ui },
    { id: "font-controls", fallback: ui },
  ];
  for (const { id, fallback } of regional) {
    vars[id] = overrides[id] ?? fallback;
  }
}

export function computeCssVars(state: AppearanceState): Record<string, string> {
  const vars: Record<string, string> = {
    radius: DEFAULT_CSS_VARS.radius,
    "radius-sm": DEFAULT_CSS_VARS["radius-sm"],
    "font-ui": DEFAULT_CSS_VARS["font-ui"],
    "font-mono": DEFAULT_CSS_VARS["font-mono"],
    "font-ui-size": DEFAULT_CSS_VARS["font-ui-size"],
    "font-mono-size": DEFAULT_CSS_VARS["font-mono-size"],
    "blur-md": DEFAULT_CSS_VARS["blur-md"],
  };

  applySemanticColors(vars, state.overrides);

  for (const token of ALL_COLOR_TOKEN_DEFS) {
    if (state.overrides[token.id]) {
      vars[token.id] = state.overrides[token.id];
    } else {
      const baseKey = token.base;
      const baseHex = foundationHex(state.foundation, baseKey);
      const computed = token.rule(baseHex);
      vars[token.id] = token.color ? applyBaseAlpha(computed, state.foundation, baseKey) : computed;
    }
  }

  applyOnSemantic(vars, state.overrides);

  applyShadows(vars, state.overrides);

  for (const token of LAYOUT_TOKENS) {
    if (state.overrides[token.id]) {
      vars[token.id] = state.overrides[token.id];
    } else if (!vars[token.id] && DEFAULT_CSS_VARS[token.id]) {
      vars[token.id] = DEFAULT_CSS_VARS[token.id];
    }
  }

  applyFontLibraryToCssVars(vars, state.overrides);

  applyRegionalFonts(vars, state.overrides);

  for (const status of STATUS_COLORS) {
    const custom = state.statusOverrides[status.id] || {};
    const borderKey = status.vars.border;
    const textKey = status.vars.text;
    const dimKey = status.vars.dim;
    if (custom.text) vars[textKey] = custom.text;
    if (custom.border) vars[borderKey] = custom.border;
    if (custom.dim) vars[dimKey] = custom.dim;
    else if (!state.overrides[dimKey]) vars[dimKey] = hexToRgba(vars[borderKey]!, 0.15);
    vars[status.vars.bg] = vars[dimKey]!;
  }

  for (const token of VERSE_COLOR_TOKENS) {
    if (state.overrides[token.id]) {
      vars[token.id] = state.overrides[token.id];
    } else {
      vars[token.id] = token.rule(vars, state.foundation);
    }
  }

  applyAliases(vars, state.overrides);

  return vars;
}

/** Infer native color-scheme from background luminance (HSL lightness). */
export function colorSchemeFromBg(bg: string | undefined): "light" | "dark" {
  const hex = colorToHexAndAlpha(bg || DEFAULT_CSS_VARS.bg).hex;
  const [, , lightness] = hexToHSL(hex);
  return lightness >= 50 ? "light" : "dark";
}

export function cssVarsToStyleBlock(vars: Record<string, string>): string {
  const scheme = colorSchemeFromBg(vars.bg);
  const lines = Object.entries(vars).map(([key, value]) => `  --${key}: ${value};`);
  return `:root {\n  color-scheme: ${scheme};\n${lines.join("\n")}\n}`;
}

/** Default :root block for critical.css / flash prevention — keep in sync with defaultTokens. */
export function defaultAppearanceCssBlock(): string {
  return cssVarsToStyleBlock(
    computeCssVars({
      foundation: defaultFoundation(),
      overrides: {},
      statusOverrides: {},
    }),
  );
}

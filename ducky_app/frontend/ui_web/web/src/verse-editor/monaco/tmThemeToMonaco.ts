import type { editor } from "monaco-editor";

import { toMonacoEditorColor } from "../../theme/colorUtils";

type TmTheme = {
  colors?: Record<string, string>;
  tokenColors?: Array<{
    scope?: string | string[];
    settings?: { foreground?: string; fontStyle?: string };
  }>;
};

/** Monaco token class → appearance CSS var (same ids as :root --verse-syntax-*). */
const MONACO_SYNTAX_CSS_MAP: Array<{ token: string; cssVar: string }> = [
  { token: "comment", cssVar: "verse-syntax-comment" },
  { token: "keyword", cssVar: "verse-syntax-keyword" },
  { token: "type", cssVar: "verse-syntax-type" },
  { token: "number", cssVar: "verse-syntax-number" },
  { token: "string", cssVar: "verse-syntax-string" },
  { token: "string.char", cssVar: "verse-syntax-string-char" },
  { token: "string.escape", cssVar: "verse-syntax-string-escape" },
  { token: "type.identifier", cssVar: "verse-syntax-type-id" },
  { token: "path", cssVar: "verse-syntax-path" },
  { token: "identifier", cssVar: "verse-syntax-identifier" },
  { token: "operator", cssVar: "verse-syntax-operator" },
  { token: "operator.arithmetic", cssVar: "verse-syntax-operator-arith" },
  { token: "operator.comparison", cssVar: "verse-syntax-operator-compare" },
  { token: "operator.logical", cssVar: "verse-syntax-operator-logical" },
  { token: "string.invalid", cssVar: "verse-syntax-error" },
  { token: "tag", cssVar: "verse-syntax-tag" },
  { token: "bracket", cssVar: "verse-syntax-bracket" },
];

function stripHash(color: string): string {
  return color.replace(/^#/, "");
}

/** Monaco token foreground must be 6-digit hex without #. */
function toMonacoHex(color: string): string | null {
  const raw = stripHash(color.trim());
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
  if (/^[0-9a-fA-F]{3}$/.test(raw)) {
    return raw
      .split("")
      .map((c) => c + c)
      .join("")
      .toLowerCase();
  }
  return null;
}

/**
 * Map a TextMate scope to a Monaco theme token name.
 * Each appearance swatch maps to exactly one token name below.
 */
export function scopeToMonacoToken(scope: string): string {
  if (scope.includes("comment")) return "comment";
  if (scope.includes("constant.character.escape")) return "string.escape";
  if (scope.includes("constant.numeric")) return "number";
  if (scope.includes("constant.language.path") || scope.endsWith(".path.verse")) return "path";
  if (scope.includes("constant.language")) return "path";
  if (scope.includes("punctuation.section.bracket") || scope.includes("punctuation.section.brace")) {
    return "bracket";
  }
  if (scope.includes("entity.name.function")) return "type.identifier";
  if (scope.includes("entity.name.tag") || scope.includes("punctuation.definition.tag")) return "tag";
  if (scope.includes("keyword.operator.logical")) return "operator.logical";
  if (scope.includes("keyword.operator.arithmetic")) return "operator.arithmetic";
  if (scope.includes("keyword.operator.comparison")) return "operator.comparison";
  if (scope.includes("keyword.operator")) return "operator";
  if (scope.includes("keyword.declaration") || scope.includes("keyword.control")) return "keyword";
  if (scope.includes("storage.type") || scope.includes("storage.modifier")) return "type";
  if (scope.includes("storage")) return "keyword";
  if (scope.includes("keyword")) return "keyword";
  if (scope.includes("string.quoted.single")) return "string.char";
  if (scope.includes("string")) return "string";
  if (scope === "path" || scope.endsWith(".path")) return "path";
  if (scope.includes("variable")) return "identifier";
  if (scope.includes("invalid")) return "string.invalid";

  const parts = scope.split(".");
  const last = parts[parts.length - 1] ?? scope;
  return last.replace(/\.verse$/, "") || "identifier";
}

export function buildThemeFromTmTheme(
  tm: TmTheme,
  cssVarOverrides: Record<string, string>,
): editor.IStandaloneThemeData {
  const c = (id: string) => cssVarOverrides[id] ?? "";
  const editorColor = (id: string) => toMonacoEditorColor(c(id), c(id));

  const ruleByToken = new Map<string, editor.ITokenThemeRule>();

  for (const entry of tm.tokenColors ?? []) {
    const scopes = Array.isArray(entry.scope) ? entry.scope : entry.scope ? [entry.scope] : [];
    const fg = entry.settings?.foreground;
    const fontStyle = entry.settings?.fontStyle;
    for (const scope of scopes) {
      if (!scope || !fg) continue;
      const hex = toMonacoHex(fg);
      if (!hex) continue;
      const token = scopeToMonacoToken(scope);
      if (!token) continue;
      ruleByToken.set(token, { token, foreground: hex, fontStyle: fontStyle || undefined });
    }
  }

  for (const { token, cssVar } of MONACO_SYNTAX_CSS_MAP) {
    const hex = toMonacoHex(c(cssVar));
    if (!hex) continue;
    ruleByToken.set(token, { token, foreground: hex });
  }

  const rules = [...ruleByToken.values()];

  return {
    base: "vs-dark",
    inherit: true,
    rules,
    colors: {
      "editor.background": editorColor("verse-editor-bg"),
      "editor.foreground": editorColor("verse-editor-fg"),
      "editorLineNumber.foreground": editorColor("verse-editor-line-number"),
      "editorLineNumber.activeForeground": editorColor("verse-editor-line-number-active"),
      "editorCursor.foreground": editorColor("verse-editor-cursor"),
      "editor.selectionBackground": editorColor("verse-editor-selection"),
      "editor.inactiveSelectionBackground": editorColor("verse-editor-selection-inactive"),
      "editor.selectionHighlightBackground": editorColor("verse-editor-selection-highlight"),
      "editor.lineHighlightBackground": editorColor("verse-editor-line-highlight"),
      "editor.lineHighlightBorder": editorColor("verse-editor-line-highlight-border"),
      "editorIndentGuide.background": editorColor("verse-editor-indent"),
      "editorIndentGuide.activeBackground": editorColor("verse-editor-indent-active"),
      "editorBracketMatch.background": editorColor("verse-editor-bracket-match-bg"),
      "editorBracketMatch.border": editorColor("verse-editor-bracket-match-border"),
      "editorError.foreground": c("verse-syntax-error"),
      "editorWarning.foreground": c("amber"),
      "editorGutter.background": editorColor("verse-editor-bg"),
      "minimap.background": editorColor("verse-editor-bg"),
      "editorWidget.background": editorColor("verse-editor-widget-bg"),
      "editorWidget.border": editorColor("verse-editor-widget-border"),
    },
  };
}

/** Resolve Monaco token from TextMate scopes (most-specific scope wins). */
export function createScopeMatcher(_tm: TmTheme): (scopes: string[]) => string {
  return (lineScopes: string[]) => {
    const ordered = [...lineScopes].reverse();
    for (const scope of ordered) {
      const token = scopeToMonacoToken(scope);
      if (token) return token;
    }
    return "";
  };
}

import type { editor } from "monaco-editor";

export const MONACO_EDITOR_FONT_SIZE = 13;

/**
 * Editor font size = the configurable mono base size (--font-mono-size) scaled by
 * the Ctrl+scroll zoom. Pass an explicit baseSize to override the live CSS var.
 */
export function monacoFontSizeForZoom(zoom: number, baseSize?: number): number {
  const base = baseSize ?? resolveMonoFontSize({});
  return Math.max(8, Math.round(base * zoom));
}

const FALLBACK_STACK = '"JetBrains Mono", Consolas, monospace';
const FALLBACK_FAMILY = "Consolas";

function readFontStack(cssVars: Record<string, string>): string {
  const fromVar = cssVars["font-mono"]?.trim();
  if (fromVar) return fromVar;
  if (typeof document !== "undefined") {
    const computed = getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim();
    if (computed) return computed;
  }
  return FALLBACK_STACK;
}

/** First family from a CSS font stack — Monaco must not receive var() or multi-family stacks. */
export function parseFirstFontFamily(stack: string): string {
  const first = stack.split(",")[0]?.trim() ?? stack.trim();
  return first.replace(/^['"]|['"]$/g, "") || FALLBACK_FAMILY;
}

export function resolveMonacoFontFamily(cssVars: Record<string, string>): string {
  return parseFirstFontFamily(readFontStack(cssVars));
}

/** Alias for terminal and other mono surfaces. */
export const resolveMonoFontFamily = resolveMonacoFontFamily;

/** Default size (px) for non-editor mono surfaces (terminal, code blocks). */
export const MONO_SURFACE_FONT_SIZE = 13;

/** Read the configurable mono base size (--font-mono-size) for the terminal / code surfaces. */
export function resolveMonoFontSize(
  cssVars: Record<string, string>,
  fallback = MONO_SURFACE_FONT_SIZE,
): number {
  let raw = cssVars["font-mono-size"]?.trim() ?? "";
  if (!raw && typeof document !== "undefined") {
    raw = getComputedStyle(document.documentElement).getPropertyValue("--font-mono-size").trim();
  }
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/** Load the mono font before Monaco measures glyph widths (avoids cursor/selection drift). */
export async function ensureMonacoFontReady(
  cssVars: Record<string, string>,
  fontSize = MONACO_EDITOR_FONT_SIZE,
): Promise<string> {
  const preferred = resolveMonacoFontFamily(cssVars);
  const candidates = [...new Set([preferred, "JetBrains Mono", "Consolas", "Cascadia Mono", "Courier New"])];

  if (typeof document === "undefined" || !document.fonts?.load) {
    return preferred;
  }

  for (const family of candidates) {
    try {
      await document.fonts.load(`${fontSize}px "${family}"`);
      await document.fonts.ready;
      if (document.fonts.check(`${fontSize}px "${family}"`)) {
        return family;
      }
    } catch {
      // try next candidate
    }
  }

  return FALLBACK_FAMILY;
}

export function applyMonacoEditorFont(
  ed: editor.IStandaloneCodeEditor,
  fontFamily: string,
  fontSize = MONACO_EDITOR_FONT_SIZE,
): void {
  ed.updateOptions({
    fontFamily,
    fontSize,
    fontLigatures: false,
    fontWeight: "normal",
  });
}

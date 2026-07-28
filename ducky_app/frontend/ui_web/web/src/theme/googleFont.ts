import { parseFirstFontFamily } from "../verse-editor/monaco/resolveMonacoFontFamily";
import { DEFAULT_CSS_VARS } from "./defaultTokens";
import type { FontEntry } from "./fontLibrary";

const BUILTIN_UI_FAMILIES = new Set(["Inter", "Segoe UI", "system-ui", "sans-serif"]);
const BUILTIN_MONO_FAMILIES = new Set(["JetBrains Mono", "Consolas", "monospace", "Cascadia Mono", "Courier New"]);
const GOOGLE_FONT_LINK_ID = "uefn-google-font-ui";
const GOOGLE_FONT_MONO_LINK_ID = "uefn-google-font-mono";
const GOOGLE_FONT_LIBRARY_PREFIX = "uefn-google-font-lib-";

export function buildUIFontStack(family: string): string {
  const name = family.trim().replace(/^['"]|['"]$/g, "");
  return `"${name}", "Segoe UI", system-ui, sans-serif`;
}

export function buildMonoFontStack(family: string): string {
  const name = family.trim().replace(/^['"]|['"]$/g, "");
  return `"${name}", Consolas, monospace`;
}

export function googleFontHref(family: string): string {
  const name = family.trim().replace(/^['"]|['"]$/g, "");
  return `https://fonts.googleapis.com/css2?family=${encodeURIComponent(name)}:wght@400;500;600;700&display=swap`;
}

/** Parse a Google Fonts share URL, CSS link, or plain family name. */
export function parseGoogleFontInput(raw: string): { family: string; href: string } | null {
  const text = raw.trim();
  if (!text) return null;

  if (/fonts\.google(?:apis)?\.com/i.test(text)) {
    try {
      const href = text.includes("://") ? text : `https://${text}`;
      const url = new URL(href);
      const familyParam = url.searchParams.get("family");
      if (!familyParam) return null;
      const family = decodeURIComponent(familyParam.split(":")[0]!.replace(/\+/g, " "));
      if (!family) return null;
      return { family, href: url.toString() };
    } catch {
      return null;
    }
  }

  const family = text.replace(/^['"]|['"]$/g, "").split(",")[0]?.trim() ?? "";
  if (!family || family.length < 2) return null;
  return { family, href: googleFontHref(family) };
}

export function primaryUIFontFamily(fontStack: string): string {
  return parseFirstFontFamily(fontStack || DEFAULT_CSS_VARS["font-ui"]);
}

export function isBuiltinUIFont(family: string): boolean {
  return BUILTIN_UI_FAMILIES.has(family);
}

export function isBuiltinMonoFont(family: string): boolean {
  return BUILTIN_MONO_FAMILIES.has(family);
}

function fontLinkSlug(family: string): string {
  return family.replace(/\s+/g, "-").toLowerCase();
}

function ensureGoogleFontLink(id: string, href: string): void {
  if (typeof document === "undefined") return;
  const existing = document.getElementById(id) as HTMLLinkElement | null;
  if (existing) {
    if (existing.href !== href) existing.href = href;
    return;
  }
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

function syncPrimaryFontLink(linkId: string, fontStack: string, isBuiltin: (family: string) => boolean): void {
  if (typeof document === "undefined") return;

  const family = parseFirstFontFamily(fontStack);
  const existing = document.getElementById(linkId) as HTMLLinkElement | null;

  if (isBuiltin(family)) {
    existing?.remove();
    return;
  }

  ensureGoogleFontLink(linkId, googleFontHref(family));
}

export function syncGoogleUIFontLink(fontStack: string): void {
  syncPrimaryFontLink(GOOGLE_FONT_LINK_ID, fontStack, isBuiltinUIFont);
}

export function syncGoogleMonoFontLink(fontStack: string): void {
  syncPrimaryFontLink(GOOGLE_FONT_MONO_LINK_ID, fontStack, isBuiltinMonoFont);
}

/** Preload every font in the library so tab previews render instantly. */
export function syncAllGoogleFontLinks(entries: FontEntry[]): void {
  if (typeof document === "undefined") return;

  const wantedIds = new Set<string>();
  for (const entry of entries) {
    const family = parseFirstFontFamily(entry.stack);
    const isBuiltin = entry.role === "ui" ? isBuiltinUIFont(family) : isBuiltinMonoFont(family);
    if (isBuiltin) continue;
    const id = `${GOOGLE_FONT_LIBRARY_PREFIX}${fontLinkSlug(family)}`;
    wantedIds.add(id);
    ensureGoogleFontLink(id, entry.href || googleFontHref(family));
  }

  for (const el of document.querySelectorAll(`link[id^="${GOOGLE_FONT_LIBRARY_PREFIX}"]`)) {
    if (!wantedIds.has(el.id)) el.remove();
  }
}

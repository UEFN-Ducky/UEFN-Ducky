import { onApiReady } from "../hooks/onApiReady";
import { isDirectMode } from "../remote/directTransport";
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

// Google fonts are never loaded from the internet: the app downloads a picked font once
// into AppData (cache_font) and the page loads it from the local panel server.
const RETRY_FAILED_FONT_MS = 60_000;
const localFontHrefs = new Map<string, Promise<string | null>>();
const failedFontAt = new Map<string, number>();
const wantedFontLinks = new Map<string, string>();

export function localFontHref(family: string): Promise<string | null> {
  const key = family.trim().toLowerCase();
  const failed = failedFontAt.get(key);
  if (failed !== undefined && Date.now() - failed < RETRY_FAILED_FONT_MS) return Promise.resolve(null);
  let pending = localFontHrefs.get(key);
  if (!pending) {
    pending = (async () => {
      // The phone panel isn't served by this PC, so it has no local copy: system font.
      if (isDirectMode()) return null;
      try {
        const api = await new Promise<Parameters<Parameters<typeof onApiReady>[0]>[0]>((resolve) => {
          onApiReady(resolve);
        });
        const res = await api.cache_font?.(family);
        return res?.ok && res.href ? res.href : null;
      } catch {
        return null;
      }
    })();
    localFontHrefs.set(key, pending);
    void pending.then((href) => {
      if (href) return;
      localFontHrefs.delete(key);
      failedFontAt.set(key, Date.now());
    });
  }
  return pending;
}

function removeFontLink(id: string): void {
  wantedFontLinks.delete(id);
  document.getElementById(id)?.remove();
}

function ensureGoogleFontLink(id: string, family: string): void {
  if (typeof document === "undefined") return;
  if (wantedFontLinks.get(id) === family) return;
  wantedFontLinks.set(id, family);
  void localFontHref(family).then((href) => {
    if (wantedFontLinks.get(id) !== family) return;
    const existing = document.getElementById(id) as HTMLLinkElement | null;
    if (!href) {
      // Not downloadable right now: fall back to the next font in the stack, try again later.
      existing?.remove();
      wantedFontLinks.delete(id);
      return;
    }
    if (existing) {
      if (existing.getAttribute("href") !== href) existing.setAttribute("href", href);
      return;
    }
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.setAttribute("href", href);
    document.head.appendChild(link);
  });
}

function syncPrimaryFontLink(linkId: string, fontStack: string, isBuiltin: (family: string) => boolean): void {
  if (typeof document === "undefined") return;

  const family = parseFirstFontFamily(fontStack);
  if (isBuiltin(family)) {
    removeFontLink(linkId);
    return;
  }

  ensureGoogleFontLink(linkId, family);
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
    ensureGoogleFontLink(id, family);
  }

  for (const id of [...wantedFontLinks.keys()]) {
    if (id.startsWith(GOOGLE_FONT_LIBRARY_PREFIX) && !wantedIds.has(id)) removeFontLink(id);
  }
  for (const el of document.querySelectorAll(`link[id^="${GOOGLE_FONT_LIBRARY_PREFIX}"]`)) {
    if (!wantedIds.has(el.id)) removeFontLink(el.id);
  }
}

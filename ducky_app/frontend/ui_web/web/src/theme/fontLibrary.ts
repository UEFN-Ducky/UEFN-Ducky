import { DEFAULT_CSS_VARS } from "./defaultTokens";
import { buildMonoFontStack, buildUIFontStack, googleFontHref, primaryUIFontFamily } from "./googleFont";
import { parseFirstFontFamily } from "../verse-editor/monaco/resolveMonacoFontFamily";

export const FONT_META_KEYS = {
  library: "_font_library",
  uiActive: "_font_ui_active",
  monoActive: "_font_mono_active",
  uiEnabled: "_font_ui_enabled",
  monoEnabled: "_font_mono_enabled",
  uiSize: "_font_ui_size",
  monoSize: "_font_mono_size",
} as const;

/** Base font sizes (px). Independent of the custom-family toggle — always applied. */
export const UI_FONT_SIZE_DEFAULT = 12;
export const MONO_FONT_SIZE_DEFAULT = 13;
export const FONT_SIZE_MIN = 9;
export const FONT_SIZE_MAX = 28;

export function clampFontSize(px: number): number {
  if (!Number.isFinite(px)) return UI_FONT_SIZE_DEFAULT;
  return Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, Math.round(px)));
}

export function defaultFontSize(role: FontRole): number {
  return role === "ui" ? UI_FONT_SIZE_DEFAULT : MONO_FONT_SIZE_DEFAULT;
}

export type FontRole = "ui" | "mono";

export interface FontEntry {
  id: string;
  name: string;
  role: FontRole;
  stack: string;
  href: string;
}

export interface FontLibraryState {
  entries: FontEntry[];
  uiActive: string | null;
  monoActive: string | null;
  uiEnabled: boolean;
  monoEnabled: boolean;
  uiSize: number;
  monoSize: number;
}

export const FONT_META_KEY_SET = new Set<string>(Object.values(FONT_META_KEYS));

function newFontId(): string {
  return `font-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function parseBool(raw: string | undefined, fallback: boolean): boolean {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return fallback;
}

function parseSize(raw: string | undefined, fallback: number): number {
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return clampFontSize(n);
}

function parseEntries(raw: string | undefined): FontEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry): entry is FontEntry =>
        !!entry &&
        typeof entry === "object" &&
        typeof (entry as FontEntry).id === "string" &&
        typeof (entry as FontEntry).name === "string" &&
        ((entry as FontEntry).role === "ui" || (entry as FontEntry).role === "mono") &&
        typeof (entry as FontEntry).stack === "string" &&
        typeof (entry as FontEntry).href === "string",
    );
  } catch {
    return [];
  }
}

export function parseFontLibrary(overrides: Record<string, string>): FontLibraryState {
  const entries = parseEntries(overrides[FONT_META_KEYS.library]);
  const uiActive = overrides[FONT_META_KEYS.uiActive] || null;
  const monoActive = overrides[FONT_META_KEYS.monoActive] || null;
  return {
    entries,
    uiActive,
    monoActive,
    uiEnabled: parseBool(overrides[FONT_META_KEYS.uiEnabled], false),
    monoEnabled: parseBool(overrides[FONT_META_KEYS.monoEnabled], false),
    uiSize: parseSize(overrides[FONT_META_KEYS.uiSize], UI_FONT_SIZE_DEFAULT),
    monoSize: parseSize(overrides[FONT_META_KEYS.monoSize], MONO_FONT_SIZE_DEFAULT),
  };
}

export function fontSizeForRole(lib: FontLibraryState, role: FontRole): number {
  return role === "ui" ? lib.uiSize : lib.monoSize;
}

export function getActiveEntry(lib: FontLibraryState, role: FontRole): FontEntry | null {
  const activeId = role === "ui" ? lib.uiActive : lib.monoActive;
  if (!activeId) return null;
  return lib.entries.find((e) => e.id === activeId && e.role === role) ?? null;
}

export function fontsForRole(lib: FontLibraryState, role: FontRole): FontEntry[] {
  return lib.entries.filter((e) => e.role === role);
}

function serializeLibrary(entries: FontEntry[]): string {
  return JSON.stringify(entries);
}

export function fontLibraryToOverridesPatch(lib: FontLibraryState): Record<string, string> {
  const patch: Record<string, string> = {
    [FONT_META_KEYS.library]: serializeLibrary(lib.entries),
    [FONT_META_KEYS.uiEnabled]: lib.uiEnabled ? "true" : "false",
    [FONT_META_KEYS.monoEnabled]: lib.monoEnabled ? "true" : "false",
    [FONT_META_KEYS.uiSize]: String(lib.uiSize),
    [FONT_META_KEYS.monoSize]: String(lib.monoSize),
  };
  if (lib.uiActive) patch[FONT_META_KEYS.uiActive] = lib.uiActive;
  if (lib.monoActive) patch[FONT_META_KEYS.monoActive] = lib.monoActive;

  const uiEntry = getActiveEntry(lib, "ui");
  const monoEntry = getActiveEntry(lib, "mono");
  if (lib.uiEnabled && uiEntry) patch["font-ui"] = uiEntry.stack;
  if (lib.monoEnabled && monoEntry) patch["font-mono"] = monoEntry.stack;

  return patch;
}

export function createFontEntry(role: FontRole, family: string, href: string): FontEntry {
  const stack = role === "ui" ? buildUIFontStack(family) : buildMonoFontStack(family);
  return {
    id: newFontId(),
    name: family,
    role,
    stack,
    href,
  };
}

export function addFontEntry(lib: FontLibraryState, entry: FontEntry): FontLibraryState {
  return { ...lib, entries: [...lib.entries, entry] };
}

export function removeFontEntry(lib: FontLibraryState, id: string): FontLibraryState {
  const removed = lib.entries.find((e) => e.id === id);
  if (!removed) return lib;
  const entries = lib.entries.filter((e) => e.id !== id);
  const next: FontLibraryState = { ...lib, entries };
  if (removed.role === "ui" && lib.uiActive === id) {
    const fallback = entries.find((e) => e.role === "ui");
    next.uiActive = fallback?.id ?? null;
    if (!fallback) next.uiEnabled = false;
  }
  if (removed.role === "mono" && lib.monoActive === id) {
    const fallback = entries.find((e) => e.role === "mono");
    next.monoActive = fallback?.id ?? null;
    if (!fallback) next.monoEnabled = false;
  }
  return next;
}

export function setActiveFont(lib: FontLibraryState, role: FontRole, id: string): FontLibraryState {
  const entry = lib.entries.find((e) => e.id === id && e.role === role);
  if (!entry) return lib;
  if (role === "ui") return { ...lib, uiActive: id, uiEnabled: true };
  return { ...lib, monoActive: id, monoEnabled: true };
}

export function setFontEnabled(lib: FontLibraryState, role: FontRole, enabled: boolean): FontLibraryState {
  if (role === "ui") return { ...lib, uiEnabled: enabled };
  return { ...lib, monoEnabled: enabled };
}

export function setFontSize(lib: FontLibraryState, role: FontRole, px: number): FontLibraryState {
  const size = clampFontSize(px);
  if (role === "ui") return { ...lib, uiSize: size };
  return { ...lib, monoSize: size };
}

/** Migrate legacy single font-ui / font-mono overrides into the library. */
export function migrateLegacyFonts(overrides: Record<string, string>): Record<string, string> {
  if (overrides[FONT_META_KEYS.library]) return overrides;

  const next = { ...overrides };
  const entries: FontEntry[] = [];
  let uiActive: string | null = null;
  let monoActive: string | null = null;
  let uiEnabled = false;
  let monoEnabled = false;

  const uiStack = overrides["font-ui"];
  if (uiStack && uiStack !== DEFAULT_CSS_VARS["font-ui"]) {
    const family = primaryUIFontFamily(uiStack);
    const entry = createFontEntry("ui", family, googleFontHref(family));
    entry.stack = uiStack;
    entries.push(entry);
    uiActive = entry.id;
    uiEnabled = true;
  }

  const monoStack = overrides["font-mono"];
  if (monoStack && monoStack !== DEFAULT_CSS_VARS["font-mono"]) {
    const family = parseFirstFontFamily(monoStack);
    const entry = createFontEntry("mono", family, googleFontHref(family));
    entry.stack = monoStack;
    entries.push(entry);
    monoActive = entry.id;
    monoEnabled = true;
  }

  if (entries.length === 0) return overrides;

  next[FONT_META_KEYS.library] = serializeLibrary(entries);
  if (uiActive) next[FONT_META_KEYS.uiActive] = uiActive;
  if (monoActive) next[FONT_META_KEYS.monoActive] = monoActive;
  next[FONT_META_KEYS.uiEnabled] = uiEnabled ? "true" : "false";
  next[FONT_META_KEYS.monoEnabled] = monoEnabled ? "true" : "false";
  return next;
}

export function applyFontLibraryToCssVars(
  vars: Record<string, string>,
  overrides: Record<string, string>,
): void {
  const lib = parseFontLibrary(overrides);

  // Base sizes apply regardless of whether a custom family is enabled.
  vars["font-ui-size"] = `${lib.uiSize}px`;
  vars["font-mono-size"] = `${lib.monoSize}px`;

  if (!lib.uiEnabled) {
    if (overrides["font-ui"]) vars["font-ui"] = overrides["font-ui"];
    else vars["font-ui"] = DEFAULT_CSS_VARS["font-ui"]!;
  } else {
    const active = getActiveEntry(lib, "ui");
    if (active) vars["font-ui"] = active.stack;
    else if (overrides["font-ui"]) vars["font-ui"] = overrides["font-ui"];
  }

  if (!lib.monoEnabled) {
    if (overrides["font-mono"]) vars["font-mono"] = overrides["font-mono"];
    else vars["font-mono"] = DEFAULT_CSS_VARS["font-mono"]!;
  } else {
    const active = getActiveEntry(lib, "mono");
    if (active) vars["font-mono"] = active.stack;
    else if (overrides["font-mono"]) vars["font-mono"] = overrides["font-mono"];
  }
}

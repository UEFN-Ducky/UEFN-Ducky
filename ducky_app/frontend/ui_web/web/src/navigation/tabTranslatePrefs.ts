/**
 * Auto-translate prefs (Translation plugin).
 *
 * Globals (Languages settings): autoTranslateAllFiles / autoTranslateAllChats
 * Per-item: allowlists when global is off; denylists when global is on.
 */

import type { PluginUiPrefValue } from "../hooks/usePluginUiPrefs";
import {
  isBinaryProjectFile,
  isImageFilePath,
  isModelFilePath,
} from "../verse-editor/utils/isVerseFile";

const PLUGIN_ID = "translation";
const STORAGE_KEY = "uefn-plugin-ui-prefs";

type SetPref = (key: string, value: PluginUiPrefValue) => void;

function readSlot(): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
    const slot = all[PLUGIN_ID];
    return slot && typeof slot === "object" ? slot : {};
  } catch {
    return {};
  }
}

function parseIdList(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw.trim()) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.map((x) => String(x || "").trim()).filter(Boolean);
  } catch {
    return [];
  }
}

function normPath(path: string): string {
  return path.replace(/\\/g, "/").trim();
}

function listHas(list: string[], id: string, pathMode: boolean): boolean {
  const key = id.trim();
  if (!key) return false;
  if (pathMode) {
    const p = normPath(key).toLowerCase();
    return list.some((x) => x.toLowerCase() === p);
  }
  return list.includes(key);
}

export function serializeIdList(ids: string[]): string {
  return JSON.stringify([...new Set(ids.map((x) => x.trim()).filter(Boolean))]);
}

/** Any text-ish file Ducky can open — Verse, py, md, ext:, etc. Not images/models/binaries. */
export function canVisualTranslateFile(path: string): boolean {
  const p = normPath(path);
  if (!p) return false;
  if (isImageFilePath(p) || isModelFilePath(p) || isBinaryProjectFile(p)) return false;
  return true;
}

export function toggleInList(list: string[], id: string): string[] {
  const key = id.trim();
  if (!key) return list;
  const lower = key.toLowerCase();
  const has = list.some((x) => x.toLowerCase() === lower);
  return has ? list.filter((x) => x.toLowerCase() !== lower) : [...list, key];
}

export function autoTranslateAllFilesFromPrefs(prefs: Record<string, unknown>): boolean {
  return prefs.autoTranslateAllFiles === true;
}

export function autoTranslateAllChatsFromPrefs(prefs: Record<string, unknown>): boolean {
  return prefs.autoTranslateAllChats === true;
}

/** Allowlist (used when global auto-files is off). */
export function autoTranslateFilesFromPrefs(prefs: Record<string, unknown>): string[] {
  return parseIdList(prefs.autoTranslateFiles).map(normPath);
}

/** Denylist (used when global auto-files is on). */
export function autoTranslateFilesOffFromPrefs(prefs: Record<string, unknown>): string[] {
  return parseIdList(prefs.autoTranslateFilesOff).map(normPath);
}

/** Allowlist (used when global auto-chats is off). */
export function autoTranslateChatsFromPrefs(prefs: Record<string, unknown>): string[] {
  return parseIdList(prefs.autoTranslateChats);
}

/** Denylist (used when global auto-chats is on). */
export function autoTranslateChatsOffFromPrefs(prefs: Record<string, unknown>): string[] {
  return parseIdList(prefs.autoTranslateChatsOff);
}

export function isAutoTranslateFile(path: string, prefs?: Record<string, unknown>): boolean {
  const p = prefs ?? readSlot();
  const key = normPath(path);
  if (!key) return false;
  if (autoTranslateAllFilesFromPrefs(p)) {
    return !listHas(autoTranslateFilesOffFromPrefs(p), key, true);
  }
  return listHas(autoTranslateFilesFromPrefs(p), key, true);
}

export function isAutoTranslateChat(chatId: string, prefs?: Record<string, unknown>): boolean {
  const p = prefs ?? readSlot();
  const id = chatId.trim();
  if (!id) return false;
  if (autoTranslateAllChatsFromPrefs(p)) {
    return !listHas(autoTranslateChatsOffFromPrefs(p), id, false);
  }
  return listHas(autoTranslateChatsFromPrefs(p), id, false);
}

/**
 * Flip auto for one file. With global-on, toggles the denylist; otherwise the allowlist.
 * Returns { autoOn } after the flip.
 */
export function toggleAutoTranslateFile(
  path: string,
  prefs: Record<string, unknown>,
  setPref: SetPref,
): boolean {
  const key = normPath(path);
  if (!key) return false;
  const wasOn = isAutoTranslateFile(key, prefs);
  if (autoTranslateAllFilesFromPrefs(prefs)) {
    setPref("autoTranslateFilesOff", serializeIdList(toggleInList(autoTranslateFilesOffFromPrefs(prefs), key)));
  } else {
    setPref("autoTranslateFiles", serializeIdList(toggleInList(autoTranslateFilesFromPrefs(prefs), key)));
  }
  return !wasOn;
}

export function toggleAutoTranslateChat(
  chatId: string,
  prefs: Record<string, unknown>,
  setPref: SetPref,
): boolean {
  const id = chatId.trim();
  if (!id) return false;
  const wasOn = isAutoTranslateChat(id, prefs);
  if (autoTranslateAllChatsFromPrefs(prefs)) {
    setPref("autoTranslateChatsOff", serializeIdList(toggleInList(autoTranslateChatsOffFromPrefs(prefs), id)));
  } else {
    setPref("autoTranslateChats", serializeIdList(toggleInList(autoTranslateChatsFromPrefs(prefs), id)));
  }
  return !wasOn;
}

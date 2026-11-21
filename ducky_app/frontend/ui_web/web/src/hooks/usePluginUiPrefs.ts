import { useCallback, useEffect, useSyncExternalStore } from "react";
import { onApiReady } from "./onApiReady";
import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";
import { pluginPrefsKey, setPluginPrefsAccount } from "./pluginPrefsStorage";

/** Legacy Discord-only key — migrated once into the prefs bag's discord slot. */
const LEGACY_DISCORD_KEY = "uefn-discord-ui-prefs";

export type PluginUiPrefValue = boolean | string | number;
export type PluginUiPrefsMap = Record<string, PluginUiPrefValue>;
type AllPrefs = Record<string, PluginUiPrefsMap>;

const listeners = new Set<() => void>();
let hydratedFromDisk = false;

function migrateLegacyDiscord(all: AllPrefs): AllPrefs {
  if (all.discord && Object.keys(all.discord).length) return all;
  try {
    const raw = localStorage.getItem(LEGACY_DISCORD_KEY);
    if (!raw) return all;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const discord: PluginUiPrefsMap = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === "boolean" || typeof v === "string" || typeof v === "number") {
        discord[k] = v;
      }
    }
    if (!Object.keys(discord).length) return all;
    const next = { ...all, discord };
    localStorage.setItem(pluginPrefsKey(), JSON.stringify(next));
    localStorage.removeItem(LEGACY_DISCORD_KEY);
    return next;
  } catch {
    return all;
  }
}

function readAll(): AllPrefs {
  try {
    const raw = localStorage.getItem(pluginPrefsKey());
    const parsed = raw ? (JSON.parse(raw) as AllPrefs) : {};
    const base = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    return migrateLegacyDiscord(base);
  } catch {
    return {};
  }
}

let cached = readAll();

/** Persist one plugin slot — never rewrite the whole bag (that wiped siblings on restart). */
function persistPluginSlot(pluginId: string, slot: PluginUiPrefsMap): void {
  const api = getApi();
  if (!api?.plugin_prefs_set) return;
  void api.plugin_prefs_set(pluginId, slot).catch(() => {
    /* ignore offline / old builds */
  });
}

function writeAll(next: AllPrefs, pluginId?: string): void {
  cached = next;
  try {
    localStorage.setItem(pluginPrefsKey(), JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
  if (pluginId && next[pluginId]) {
    persistPluginSlot(pluginId, next[pluginId]);
  }
  for (const fn of [...listeners]) fn();
  // Same event the plugin iframe bridge fires — shell.boot scripts (e.g. Translation) listen.
  if (pluginId) {
    window.dispatchEvent(new CustomEvent("uefn-plugin-prefs", { detail: { pluginId } }));
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getSnapshot(): AllPrefs {
  return cached;
}

function applyDiskPrefs(disk: AllPrefs): void {
  const local = readAll();
  // Disk wins for keys that exist there (localStorage is wiped by some WebView restarts).
  const merged: AllPrefs = { ...local };
  for (const [pid, slot] of Object.entries(disk)) {
    if (!pid || !slot || typeof slot !== "object") continue;
    merged[pid] = { ...(merged[pid] ?? {}), ...slot };
  }
  cached = merged;
  try {
    localStorage.setItem(pluginPrefsKey(), JSON.stringify(merged));
  } catch {
    /* ignore */
  }
  for (const fn of [...listeners]) fn();
  for (const pid of Object.keys(merged)) {
    window.dispatchEvent(new CustomEvent("uefn-plugin-prefs", { detail: { pluginId: pid } }));
  }
}

/** Prefs belong to one account: point the bag at `account` and repaint from it. */
function switchPrefsAccount(account: string): void {
  if (!setPluginPrefsAccount(account)) return;
  const before = Object.keys(cached);
  cached = readAll();
  for (const fn of [...listeners]) fn();
  for (const pid of new Set([...before, ...Object.keys(cached)])) {
    window.dispatchEvent(new CustomEvent("uefn-plugin-prefs", { detail: { pluginId: pid } }));
  }
}

let accountWatch = false;

/** Account switch in the app: drop the other account's bag, reload this account's. */
function watchAccountSwitches(): void {
  if (accountWatch) return;
  accountWatch = true;
  installPanelPushBus();
  subscribePanelPush((event) => {
    if (event.type === "plugin_prefs_changed") {
      // A plugin's backend changed its own settings (a tool, setup): the open Settings page follows.
      const pid = typeof event.plugin_id === "string" ? event.plugin_id.trim().toLowerCase() : "";
      const slot = event.prefs;
      if (pid && slot && typeof slot === "object" && !Array.isArray(slot)) {
        applyDiskPrefs({ [pid]: slot as unknown as PluginUiPrefsMap });
      }
      return;
    }
    if (event.type !== "duckyos_account_changed") return;
    if (typeof event.account === "string") switchPrefsAccount(event.account);
    hydratedFromDisk = false;
    hydratePluginUiPrefsFromDisk();
  });
}

/** Load AppData prefs into localStorage once the panel API is up. */
export function hydratePluginUiPrefsFromDisk(): void {
  if (hydratedFromDisk) return;
  const api = getApi();
  if (!api?.plugin_prefs_get_all) return;
  hydratedFromDisk = true;
  watchAccountSwitches();
  void Promise.resolve(api.duckyos_get_status?.())
    .then((status) => {
      if (typeof status?.account === "string") switchPrefsAccount(status.account);
    })
    .catch(() => undefined)
    .then(() => api.plugin_prefs_get_all!())
    .then((res) => {
      if (!res?.ok || !res.prefs || typeof res.prefs !== "object") {
        window.dispatchEvent(new CustomEvent("uefn-plugin-prefs-hydrated"));
        return;
      }
      applyDiskPrefs(res.prefs as AllPrefs);
      // Seed disk from local only when disk had nothing (per-plugin write, no wipe).
      const disk = res.prefs as AllPrefs;
      const local = readAll();
      for (const [pid, slot] of Object.entries(local)) {
        if (!pid || !slot || typeof slot !== "object") continue;
        if (disk[pid] && Object.keys(disk[pid]).length) continue;
        persistPluginSlot(pid, slot);
      }
      window.dispatchEvent(new CustomEvent("uefn-plugin-prefs-hydrated"));
    })
    .catch(() => {
      hydratedFromDisk = false;
      window.dispatchEvent(new CustomEvent("uefn-plugin-prefs-hydrated"));
    });
}

/** Re-read every plugin's saved prefs (a Settings page opening after a backend rewrote them). */
export function refreshPluginUiPrefsFromDisk(): void {
  const api = getApi();
  if (!api?.plugin_prefs_get_all) return;
  void Promise.resolve(api.plugin_prefs_get_all())
    .then((res) => {
      if (res?.ok && res.prefs && typeof res.prefs === "object") applyDiskPrefs(res.prefs as AllPrefs);
    })
    .catch(() => undefined);
}

/** Call once near app root so Languages / Discord prefs survive restart. */
export function useHydratePluginUiPrefs(): void {
  useEffect(() => {
    return onApiReady(() => {
      hydratePluginUiPrefsFromDisk();
    });
  }, []);
}

/** Patch one plugin slot (keeps React cache + disk + uefn-plugin-prefs in sync). */
export function patchPluginUiPrefs(pluginId: string, patch: PluginUiPrefsMap): void {
  const pid = pluginId.trim().toLowerCase();
  if (!pid || !patch || !Object.keys(patch).length) return;
  const prev = readAll();
  const cur = { ...(prev[pid] ?? {}), ...patch };
  writeAll({ ...prev, [pid]: cur }, pid);
}

/** Per-plugin UI prefs (localStorage + AppData disk). Used by declarative settings.sections. */
export function usePluginUiPrefs(pluginId: string): {
  prefs: PluginUiPrefsMap;
  setPref: (id: string, value: PluginUiPrefValue) => void;
  setPrefs: (patch: PluginUiPrefsMap) => void;
  getBool: (id: string, defaultValue?: boolean) => boolean;
} {
  const all = useSyncExternalStore(subscribe, getSnapshot, (): AllPrefs => ({}));
  const prefs = all[pluginId] ?? {};

  const setPref = useCallback(
    (id: string, value: PluginUiPrefValue) => {
      const pid = pluginId.trim().toLowerCase();
      if (!pid || !id) return;
      const prev = readAll();
      const cur = { ...(prev[pid] ?? {}) };
      cur[id] = value;
      writeAll({ ...prev, [pid]: cur }, pid);
    },
    [pluginId],
  );

  const setPrefs = useCallback(
    (patch: PluginUiPrefsMap) => {
      const pid = pluginId.trim().toLowerCase();
      if (!pid || !patch || !Object.keys(patch).length) return;
      const prev = readAll();
      const cur = { ...(prev[pid] ?? {}), ...patch };
      writeAll({ ...prev, [pid]: cur }, pid);
    },
    [pluginId],
  );

  const getBool = useCallback(
    (id: string, defaultValue = true): boolean => {
      const v = prefs[id];
      if (typeof v === "boolean") return v;
      return defaultValue;
    },
    [prefs],
  );

  return { prefs, setPref, setPrefs, getBool };
}


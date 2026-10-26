// Open a plugin webview editor tab. Header buttons with action `panel:<id>`
// call requestOpenPluginUiTab(pluginId, panelId); ChatView registers the opener.

export type OpenPluginUiTabOptions = {
  /** An agent's open: add the tab behind the one the person is on (nothing moves when it is
   *  already open anywhere) and load it hidden so its panel runs. */
  background?: boolean;
};

type Opener = (pluginId: string, panelId: string, title?: string, options?: OpenPluginUiTabOptions) => void;

let opener: Opener | null = null;
let lastKey = "";
let lastAt = 0;
/** Plugin tabs opened in the background: mounted hidden, never shown until the person picks them. */
const backgroundMounts = new Set<string>();

export function registerOpenPluginUiTab(fn: Opener): () => void {
  opener = fn;
  return () => {
    if (opener === fn) opener = null;
  };
}

/** This window can open plugin tabs (the main window registers the opener). */
export function hasPluginUiTabOpener(): boolean {
  return opener !== null;
}

export function requestOpenPluginUiTab(
  pluginId: string,
  panelId: string,
  title?: string,
  options?: OpenPluginUiTabOptions,
): void {
  const pid = pluginId.trim().toLowerCase();
  const panel = panelId.trim().toLowerCase();
  if (!pid || !panel) return;
  const key = `${pid}:${panel}:${options?.background ? "bg" : ""}`;
  const now = Date.now();
  if (key === lastKey && now - lastAt < 400) return;
  lastKey = key;
  lastAt = now;
  opener?.(pid, panel, title, options);
}

const mountListeners = new Set<() => void>();
let mountVersion = 0;

/** Load this tab hidden (also one restored at startup and never shown yet). */
export function markBackgroundMount(tabId: string): void {
  if (backgroundMounts.has(tabId)) return;
  backgroundMounts.add(tabId);
  mountVersion += 1;
  for (const fn of mountListeners) fn();
}

/** The editor keeps this tab mounted (hidden) though it was never shown. */
export function wantsBackgroundMount(tabId: string): boolean {
  return backgroundMounts.has(tabId);
}

export function subscribeBackgroundMounts(fn: () => void): () => void {
  mountListeners.add(fn);
  return () => {
    mountListeners.delete(fn);
  };
}

export function backgroundMountVersion(): number {
  return mountVersion;
}

declare global {
  interface Window {
    /** shell.boot helpers (Browser "+ New browser" when no tab exists yet). */
    __duckyOpenPluginUiTab?: typeof requestOpenPluginUiTab;
  }
}

if (typeof window !== "undefined") {
  window.__duckyOpenPluginUiTab = requestOpenPluginUiTab;
}

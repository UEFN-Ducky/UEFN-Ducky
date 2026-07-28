// Open a plugin webview editor tab. Header buttons with action `panel:<id>`
// call requestOpenPluginUiTab(pluginId, panelId); ChatView registers the opener.

let opener: ((pluginId: string, panelId: string, title?: string) => void) | null = null;
let lastKey = "";
let lastAt = 0;

export function registerOpenPluginUiTab(
  fn: (pluginId: string, panelId: string, title?: string) => void,
): () => void {
  opener = fn;
  return () => {
    if (opener === fn) opener = null;
  };
}

export function requestOpenPluginUiTab(
  pluginId: string,
  panelId: string,
  title?: string,
): void {
  const pid = pluginId.trim().toLowerCase();
  const panel = panelId.trim().toLowerCase();
  if (!pid || !panel) return;
  const key = `${pid}:${panel}`;
  const now = Date.now();
  if (key === lastKey && now - lastAt < 400) return;
  lastKey = key;
  lastAt = now;
  opener?.(pid, panel, title);
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

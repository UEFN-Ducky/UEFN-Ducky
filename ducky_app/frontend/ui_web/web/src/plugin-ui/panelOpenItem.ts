// An item a reference asked a plugin panel to show (`plugin:<plugin>/<panel>:<item>`).
// The panel's pane takes it once its page has loaded and posts bridge event
// `panel.open_item {item}`; a panel already open gets it at once.

const pending = new Map<string, string>();
const listeners = new Set<(key: string) => void>();

export function panelItemKey(pluginId: string, panelId: string): string {
  return `${pluginId.trim().toLowerCase()}:${panelId.trim().toLowerCase()}`;
}

export function requestPanelItem(pluginId: string, panelId: string, item: string): void {
  const key = panelItemKey(pluginId, panelId);
  if (!item) return;
  pending.set(key, item);
  for (const fn of listeners) fn(key);
}

/** The item waiting for this panel, once (null when none). */
export function takePanelItem(key: string): string | null {
  const item = pending.get(key) ?? null;
  pending.delete(key);
  return item;
}

export function subscribePanelItems(fn: (key: string) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

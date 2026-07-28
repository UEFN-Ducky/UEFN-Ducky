/** Contribution row from host `ui_panels` (plugin.json `contributes.ui.panels`). */
export type PluginUiPanel = {
  id: string;
  title?: string;
  icon?: string;
  /** Relative path inside the plugin dir, e.g. `ui/index.html`. */
  entry: string;
  plugin_id?: string;
  version?: number;
};

/** Tab id encoding: `plugin:<pluginId>:<panelId>`. */
export function pluginUiTabId(pluginId: string, panelId: string): string {
  return `plugin:${pluginId}:${panelId}`;
}

/** Extra tab of the same panel (browser "open in new tab") — `…:<panelId>~<n>`. */
export function pluginUiInstanceTabId(pluginId: string, panelId: string): string {
  return `plugin:${pluginId}:${panelId}~${Math.random().toString(36).slice(2, 8)}`;
}

export function parsePluginUiTabId(tabId: string): { pluginId: string; panelId: string } | null {
  if (!tabId.startsWith("plugin:")) return null;
  const rest = tabId.slice("plugin:".length);
  const i = rest.indexOf(":");
  if (i <= 0) return null;
  const pluginId = rest.slice(0, i).trim().toLowerCase();
  // Instance tabs (`browser~1f3a`) resolve to the same contributed panel; the full
  // tab id stays the pane identity.
  const panelId = rest.slice(i + 1).trim().toLowerCase().split("~")[0];
  if (!pluginId || !panelId) return null;
  return { pluginId, panelId };
}

export type BridgeRequest = {
  channel: string;
  id: string;
  method: string;
  params?: Record<string, unknown>;
};

export type BridgeResponse = {
  channel: string;
  id: string;
  ok: boolean;
  result?: unknown;
  error?: string;
};

export function isBridgeRequest(data: unknown): data is BridgeRequest {
  if (!data || typeof data !== "object") return false;
  const o = data as Record<string, unknown>;
  return typeof o.channel === "string" && typeof o.id === "string" && typeof o.method === "string";
}

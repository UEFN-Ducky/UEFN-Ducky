import type { EditorTab } from "../types/panel";
import { parsePluginUiTabId } from "./types";

/**
 * Tabs that belong to a Store plugin which is no longer enabled
 * (disabled or uninstalled). Keep in sync with every plugin that opens
 * an editor tab (browser ui.panels, discord editor.kind, translation overlays, …).
 */
export function isOrphanedPluginTab(
  tab: EditorTab,
  enabledIds: ReadonlySet<string>,
): boolean {
  if (tab.kind === "plugin") {
    const parsed = parsePluginUiTabId(tab.id);
    if (!parsed) return true;
    return !enabledIds.has(parsed.pluginId);
  }
  if (tab.kind === "discord") {
    return !enabledIds.has("discord");
  }
  // Visual Verse overlay is Translation-owned — close when that plugin is gone.
  if (tab.kind === "verse-translated") {
    return !enabledIds.has("translation");
  }
  return false;
}

export function orphanedPluginTabs(
  tabs: readonly EditorTab[],
  enabledIds: readonly string[],
): EditorTab[] {
  const enabled = new Set(
    enabledIds.map((id) => id.trim().toLowerCase()).filter(Boolean),
  );
  return tabs.filter((t) => isOrphanedPluginTab(t, enabled));
}

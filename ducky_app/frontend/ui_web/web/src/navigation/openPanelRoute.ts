/**
 * Open a panel route by name — one table for ducky_ui_navigate, agent tours and Show me.
 *
 * Routes: settings, settings.<section> (General, Store, LLMs, MCPs, Skills, Appearance,
 * Audio, Duckies, Plans, Memory, Languages, Log & Errors, App data), settings.store with
 * item_id = a Store slug (that plugin's page), settings.tab with item_id = any Settings tab
 * (a plugin's own tab, "Audio"…), plans, changes, workflows, chat (item_id = chat id),
 * files (item_id = project file path), chats.
 */
import { openLlmsProviderSettings, requestOpenSettings } from "./openSettingsTab";
import { requestOpenChangesTab } from "./openChangesTab";
import { requestOpenWorkflowsTab } from "./openWorkflowsTab";
import { requestOpenChatTab, requestOpenProjectFile } from "./openChatReference";
import { requestOpenSidebarPanel } from "./openSidebarPanel";
import { handleDeepLink } from "./deepLinks";

/** settings.* route → Settings tab label. */
export const SETTINGS_TAB: Record<string, string> = {
  settings: "General",
  "settings.general": "General",
  "settings.store": "Store",
  "settings.llms": "LLMs",
  "settings.mcp": "LLMs",
  "settings.mcp_plugins": "LLMs",
  "settings.skills": "LLMs",
  "settings.appearance": "Appearance",
  "settings.audio": "Audio",
  "settings.duckies": "Duckies",
  "settings.plans": "Plans",
  "settings.memory": "LLMs",
  "settings.languages": "Languages",
  "settings.log_errors": "General",
  "settings.app_data": "General",
  "settings.permissions": "General",
  plans: "Plans",
};

/** Sub-section for tabs that have inner tabs (LLMs, General → Log & Errors). */
export const SETTINGS_SECTION: Record<string, string> = {
  "settings.general": "general",
  "settings.llms": "llms",
  "settings.mcp": "mcps",
  "settings.mcp_plugins": "mcps",
  "settings.skills": "skills",
  "settings.plans": "working",
  plans: "working",
  "settings.memory": "entries",
  "settings.log_errors": "errors",
  "settings.app_data": "app_data",
  "settings.permissions": "permissions",
};

export type RouteResult = { ok: boolean; route: string; tab?: string; item_id?: string; dispatched?: boolean; error?: string };

export function openPanelRoute(route: string, itemId = ""): RouteResult {
  const r = (route || "").trim();
  const item = (itemId || "").trim();
  if (r === "settings.tab" && item) {
    requestOpenSettings(item);
    return { ok: true, route: r, tab: item };
  }
  if (r === "settings.store" && item) {
    // The Store's own link opens that plugin's page (installing is a separate /install/ link).
    handleDeepLink(`uefn-ducky://store/${item.toLowerCase()}`);
    return { ok: true, route: r, tab: "Store", item_id: item };
  }
  const tab = SETTINGS_TAB[r];
  if (tab) {
    if (r === "settings.llms" && item) {
      openLlmsProviderSettings(item);
      return { ok: true, route: r, tab, item_id: item };
    }
    requestOpenSettings(tab as Parameters<typeof requestOpenSettings>[0]);
    const section = SETTINGS_SECTION[r];
    if (section) {
      window.dispatchEvent(new CustomEvent("ducky:settings-section", { detail: { tab, section } }));
    }
    return { ok: true, route: r, tab };
  }
  if (r === "changes") {
    requestOpenChangesTab();
    return { ok: true, route: r };
  }
  if (r === "workflows") {
    requestOpenWorkflowsTab();
    return { ok: true, route: r };
  }
  if (r === "chat" && item) {
    return requestOpenChatTab(item) ? { ok: true, route: r, item_id: item } : { ok: false, route: r, error: "chats aren't ready yet" };
  }
  if (r === "files" && item) {
    requestOpenSidebarPanel("files");
    return requestOpenProjectFile(item) ? { ok: true, route: r, item_id: item } : { ok: false, route: r, error: "files aren't ready yet" };
  }
  if (r === "files" || r === "chats") {
    requestOpenSidebarPanel(r);
    return { ok: true, route: r };
  }
  window.dispatchEvent(new CustomEvent("ducky:navigate", { detail: { route: r, item_id: item } }));
  return { ok: true, route: r, dispatched: true };
}

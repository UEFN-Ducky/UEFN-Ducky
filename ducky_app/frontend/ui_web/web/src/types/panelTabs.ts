import type { ChatLayoutMode } from "./panel";

/** The header's left button toggles what is on screen: the pane shows only when the layout
 *  mode and the dock both say open, and a fresh install starts it closed with the mode "full". */
export function nextChatLayoutMode(mode: ChatLayoutMode, leftRailOpen: boolean): ChatLayoutMode {
  return mode !== "sidebarHidden" && leftRailOpen ? "sidebarHidden" : "full";
}

export function chatTabId(chatId: string): string {
  return `chat:${chatId}`;
}

export function fileTabId(relativePath: string): string {
  // Lowercased: Windows paths are case-insensitive, and the same file reaches here
  // with on-disk casing (file tree) or lowercase registry keys (Problems panel) —
  // both must resolve to ONE tab, not duplicates.
  return `file:${relativePath.replace(/\\/g, "/").toLowerCase()}`;
}

export function terminalTabId(sessionId: string): string {
  return `terminal:${sessionId}`;
}

export function planTabId(chatId: string): string {
  return `plan:${chatId}`;
}

/** Per-provider usage dashboard tab (`usage:<providerId>`). */
export function usageTabId(providerId: string): string {
  return `usage:${providerId.trim().toLowerCase()}`;
}

export function usageProviderIdFromTab(tabId: string): string {
  return tabId.startsWith("usage:") ? tabId.slice(6) : "";
}

/** Singleton Settings editor tab (VS Code–style). */
export function settingsTabId(): string {
  return "settings:main";
}

/** Singleton Changes editor tab: one project-wide ledger, never one per chat. */
export function changesTabId(): string {
  return "changes:main";
}

/** Singleton Workflows editor tab. */
export function workflowsTabId(): string {
  return "workflows:main";
}

/** Tab ids saved by older builds (separate Automations and Pipelines tabs). */
export function isLegacyWorkflowsTabId(id: string): boolean {
  return id === "automations:main" || id === "pipelines:main" || id.startsWith("automations:") || id.startsWith("pipelines:");
}

/** Discord editor tab id — one tab per bot (`discord:<botId>`). Legacy `discord:main` = default. */
export function discordTabId(botId?: string): string {
  const bid = (botId || "default").trim() || "default";
  if (bid === "default" || bid === "main") return "discord:main";
  return `discord:${bid}`;
}

export function discordBotIdFromTab(tabId: string): string {
  if (!tabId.startsWith("discord:")) return "default";
  const rest = tabId.slice("discord:".length).trim();
  if (!rest || rest === "main") return "default";
  return rest;
}

/** Library ducky profile popped out as an editor tab (`ducky-profile:<profileId>`). */
export function duckyProfileTabId(profileId: string): string {
  return `ducky-profile:${profileId.trim()}`;
}

export function duckyProfileIdFromTab(tabId: string): string {
  return tabId.startsWith("ducky-profile:") ? tabId.slice("ducky-profile:".length) : "";
}

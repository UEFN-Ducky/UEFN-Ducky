import type { EditorTab } from "../types/panel";

type ChatRow = { id: string; name: string; duckyStyle?: string; isGroup?: boolean };

/**
 * Chat tabs carry their chat's current name, look and group flag from the chat list.
 * Returns the new tab list, or null when nothing changed.
 *
 * Runs when the list changes AND when a chat tab opens: a chat opened by id alone (a clicked
 * notification, Waiting for you, an agent) was named after its raw id, and a chat already in
 * the list never changed it again, so the tab kept the id (Nov 6 2026).
 */
export function syncChatTabTitles(tabs: readonly EditorTab[], chats: readonly ChatRow[]): EditorTab[] | null {
  const byId = new Map(chats.map((c) => [c.id, c]));
  let changed = false;
  const next = tabs.map((tab) => {
    if (tab.kind !== "chat" || !tab.chatId) return tab;
    const chat = byId.get(tab.chatId);
    if (!chat) return tab;
    const isGroup = Boolean(chat.isGroup);
    if (tab.name === chat.name && tab.duckyStyle === chat.duckyStyle && Boolean(tab.isGroup) === isGroup) return tab;
    changed = true;
    return { ...tab, name: chat.name, duckyStyle: chat.duckyStyle, isGroup };
  });
  return changed ? next : null;
}

/** A key that changes when a chat tab opens, closes or is renamed (and not otherwise). */
export function chatTabsKey(tabs: readonly EditorTab[]): string {
  return tabs
    .filter((t) => t.kind === "chat")
    .map((t) => `${t.chatId ?? ""}:${t.name}`)
    .join("\0");
}

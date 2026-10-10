import type { FolderItem } from "../types/panel";

export function anyRunningUnderChat(
  chatId: string,
  runningChatIds: ReadonlySet<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): boolean {
  return firstUnderChat(chatId, runningChatIds, childrenByParent) !== "";
}

export function anyRunningUnderFolder(
  folder: FolderItem,
  runningChatIds: ReadonlySet<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): boolean {
  return firstUnderFolder(folder, runningChatIds, childrenByParent) !== "";
}

/** The first chat in `ids` at or under this chat (its sub-agents), or ''. */
export function firstUnderChat(
  chatId: string,
  ids: ReadonlySet<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): string {
  if (ids.has(chatId)) return chatId;
  for (const child of childrenByParent.get(chatId) || []) {
    const hit = firstUnderChat(child.id, ids, childrenByParent);
    if (hit) return hit;
  }
  return "";
}

/** The first chat in `ids` anywhere in this folder (a group's members, nested folders), or ''. */
export function firstUnderFolder(
  folder: FolderItem,
  ids: ReadonlySet<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): string {
  for (const chat of folder.chats) {
    const hit = firstUnderChat(chat.id, ids, childrenByParent);
    if (hit) return hit;
  }
  for (const child of folder.children) {
    const hit = firstUnderFolder(child, ids, childrenByParent);
    if (hit) return hit;
  }
  return "";
}

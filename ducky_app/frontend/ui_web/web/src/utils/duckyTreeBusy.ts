import type { FolderItem } from "../types/panel";

export function anyRunningUnderChat(
  chatId: string,
  runningChatIds: Set<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): boolean {
  if (runningChatIds.has(chatId)) return true;
  for (const child of childrenByParent.get(chatId) || []) {
    if (anyRunningUnderChat(child.id, runningChatIds, childrenByParent)) return true;
  }
  return false;
}

export function anyRunningUnderFolder(
  folder: FolderItem,
  runningChatIds: Set<string>,
  childrenByParent: Map<string, FolderItem["chats"]>,
): boolean {
  for (const chat of folder.chats) {
    if (anyRunningUnderChat(chat.id, runningChatIds, childrenByParent)) return true;
  }
  for (const child of folder.children) {
    if (anyRunningUnderFolder(child, runningChatIds, childrenByParent)) return true;
  }
  return false;
}

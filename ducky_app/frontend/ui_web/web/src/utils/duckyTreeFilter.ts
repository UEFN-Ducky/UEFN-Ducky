import { fuzzyScore } from "../components/quick-open/quickOpenUtils";
import type { ChatTab, FolderItem } from "../types/panel";
import { findChatAncestorFolderIds } from "./sidebarTree";

function matchesQuery(query: string, name: string): boolean {
  const q = query.trim();
  if (!q) return true;
  return fuzzyScore(q, name) > 0;
}

export function duckyNameMatches(query: string, name: string): boolean {
  return matchesQuery(query, name);
}

function findFolderAncestorIds(folders: FolderItem[], folderId: string): string[] {
  const walk = (items: FolderItem[], ancestors: string[]): string[] | null => {
    for (const folder of items) {
      if (folder.id === folderId) return ancestors;
      const nested = walk(folder.children, [...ancestors, folder.id]);
      if (nested) return nested;
    }
    return null;
  };
  return walk(folders, []) ?? [];
}

export function collectVisibleDuckyIds(
  query: string,
  folders: FolderItem[],
  rootChats: FolderItem["chats"],
): { chatIds: Set<string>; folderIds: Set<string> } {
  const q = query.trim();
  const chatIds = new Set<string>();
  const folderIds = new Set<string>();
  if (!q) return { chatIds, folderIds };

  const allChats: ChatTab[] = [...rootChats];
  const walkChats = (items: FolderItem[]) => {
    for (const folder of items) {
      allChats.push(...folder.chats);
      walkChats(folder.children);
    }
  };
  walkChats(folders);

  for (const chat of allChats) {
    if (!matchesQuery(q, chat.name)) continue;
    chatIds.add(chat.id);
    for (const folderId of findChatAncestorFolderIds(folders, rootChats, chat.id)) {
      folderIds.add(folderId);
    }
  }

  const walkFolders = (items: FolderItem[]) => {
    for (const folder of items) {
      if (matchesQuery(q, folder.name)) {
        folderIds.add(folder.id);
        for (const ancestorId of findFolderAncestorIds(folders, folder.id)) {
          folderIds.add(ancestorId);
        }
      }
      walkFolders(folder.children);
    }
  };
  walkFolders(folders);

  return { chatIds, folderIds };
}

function folderHasVisibleDescendant(
  folder: FolderItem,
  query: string,
  visibleChatIds: Set<string>,
  visibleFolderIds: Set<string>,
): boolean {
  for (const chat of folder.chats) {
    if (visibleChatIds.has(chat.id) || matchesQuery(query, chat.name)) return true;
  }
  for (const child of folder.children) {
    if (visibleFolderIds.has(child.id) || matchesQuery(query, child.name)) return true;
    if (folderHasVisibleDescendant(child, query, visibleChatIds, visibleFolderIds)) return true;
  }
  return false;
}

export function shouldShowFolder(
  folder: FolderItem,
  query: string,
  visibleChatIds: Set<string>,
  visibleFolderIds: Set<string>,
): boolean {
  const q = query.trim();
  if (!q) return true;
  if (visibleFolderIds.has(folder.id)) return true;
  return folderHasVisibleDescendant(folder, q, visibleChatIds, visibleFolderIds);
}

export function shouldShowChat(
  chat: ChatTab,
  query: string,
  visibleChatIds: Set<string>,
): boolean {
  const q = query.trim();
  if (!q) return true;
  return visibleChatIds.has(chat.id);
}

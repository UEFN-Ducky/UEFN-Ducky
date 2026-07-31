import type { FolderDto, FolderItem, SidebarLayoutPatch } from "../types/panel";
import { ARCHIVE_FOLDER_ID, isArchiveFolderId } from "./archiveFolder";

export function findFolderById(folders: FolderItem[], id: string): FolderItem | null {
  for (const folder of folders) {
    if (folder.id === id) return folder;
    const nested = findFolderById(folder.children, id);
    if (nested) return nested;
  }
  return null;
}

/** Folder ids that must be expanded to reveal a chat in the duckies tree. */
export function findChatAncestorFolderIds(
  folders: FolderItem[],
  rootChats: FolderItem["chats"],
  chatId: string,
): string[] {
  const walk = (items: FolderItem[], ancestors: string[]): string[] | null => {
    for (const folder of items) {
      if (folder.chats.some((chat) => chat.id === chatId)) {
        return [...ancestors, folder.id];
      }
      const nested = walk(folder.children, [...ancestors, folder.id]);
      if (nested) return nested;
    }
    return null;
  };

  if (rootChats.some((chat) => chat.id === chatId)) return [];
  return walk(folders, []) ?? [];
}

export function expandFoldersById(folders: FolderItem[], folderIds: ReadonlySet<string>): FolderItem[] {
  return folders.map((folder) => ({
    ...folder,
    expanded: folderIds.has(folder.id) ? true : folder.expanded,
    children: expandFoldersById(folder.children, folderIds),
  }));
}

export function maxExpandedFolderDepth(folders: FolderItem[], depth = 0): number {
  let max = -1;
  for (const folder of folders) {
    if (folder.expanded) {
      max = Math.max(max, depth);
      max = Math.max(max, maxExpandedFolderDepth(folder.children, depth + 1));
    }
  }
  return max;
}

function collapseChatFoldersOneLevel(folders: FolderItem[], maxDepth: number, depth = 0): FolderItem[] {
  return folders.map((folder) => ({
    ...folder,
    expanded: depth === maxDepth ? false : folder.expanded,
    children: collapseChatFoldersOneLevel(folder.children, maxDepth, depth + 1),
  }));
}

function expandChatFoldersOneLevel(
  folders: FolderItem[],
  targetDepth: number,
  depth = 0,
  parentExpanded = true,
): FolderItem[] {
  return folders.map((folder) => {
    const expanded = depth === targetDepth && parentExpanded ? true : folder.expanded;
    return {
      ...folder,
      expanded,
      children: expandChatFoldersOneLevel(folder.children, targetDepth, depth + 1, expanded),
    };
  });
}

export function toggleChatFolderLevels(folders: FolderItem[]): FolderItem[] {
  const maxDepth = maxExpandedFolderDepth(folders);
  if (maxDepth >= 0) return collapseChatFoldersOneLevel(folders, maxDepth);
  return expandChatFoldersOneLevel(folders, 0);
}

export function chatFolderSiblingNames(folders: FolderItem[], parentId: string): string[] {
  if (!parentId) return folders.map((f) => f.name);
  const parent = findFolderById(folders, parentId);
  return parent ? parent.children.map((c) => c.name) : folders.map((f) => f.name);
}

/**
 * Place a just-created folder into the tree without waiting for a reload, so the
 * row is on screen (and namable by the next create) the moment the API returns.
 */
export function insertChatFolder(
  folders: FolderItem[],
  parentId: string,
  folder: Omit<FolderItem, "parentId" | "sortOrder">,
): FolderItem[] {
  const asChildOf = (parent: string, siblings: FolderItem[]): FolderItem => ({
    ...folder,
    parentId: parent,
    sortOrder: siblings.reduce((max, f) => Math.max(max, f.sortOrder), 0) + 1,
  });

  if (parentId && findFolderById(folders, parentId)) {
    const walk = (items: FolderItem[]): FolderItem[] =>
      items.map((item) =>
        item.id === parentId
          ? { ...item, expanded: true, children: [...item.children, asChildOf(parentId, item.children)] }
          : { ...item, children: walk(item.children) },
      );
    return walk(folders);
  }
  return [...folders, asChildOf("", folders)];
}

export function chatNamesInFolder(
  folders: FolderItem[],
  folderId: string,
  rootChats: FolderItem["chats"] = [],
): string[] {
  if (!folderId) return rootChats.map((c) => c.name);
  const folder = findFolderById(folders, folderId);
  return folder ? folder.chats.map((c) => c.name) : [];
}

export type SidebarDragKind = "folder" | "chat";

export function dragId(kind: SidebarDragKind, id: string): string {
  return `${kind}:${id}`;
}

export function parseDragId(raw: string): { kind: SidebarDragKind; id: string } | null {
  const idx = raw.indexOf(":");
  if (idx <= 0) return null;
  const kind = raw.slice(0, idx) as SidebarDragKind;
  if (kind !== "folder" && kind !== "chat") return null;
  return { kind, id: raw.slice(idx + 1) };
}

export function nestDropId(folderId: string): string {
  return `nest:${folderId || "root"}`;
}

export function parseNestDropId(raw: string): string | null {
  if (!raw.startsWith("nest:")) return null;
  const id = raw.slice(5);
  return id === "root" ? "" : id;
}

export function buildFolderTree(
  folderRows: FolderDto[],
  chatsByFolder: Map<string, FolderItem["chats"]>,
  expandedById: Map<string, boolean>,
): FolderItem[] {
  const nodes = new Map<string, FolderItem>();
  for (const row of folderRows) {
    if (isArchiveFolderId(row.id)) continue;
    const hubId = (row.group_hub_id || "").trim();
    const rawChats = chatsByFolder.get(row.id) ?? [];
    // Hub chat is the folder itself — don't list it as a child ducky.
    const chats = hubId ? rawChats.filter((c) => c.id !== hubId && !c.isGroup) : rawChats;
    nodes.set(row.id, {
      id: row.id,
      name: row.name,
      parentId: row.parent_id || "",
      sortOrder: Number(row.sort_order) || 0,
      expanded: expandedById.has(row.id) ? expandedById.get(row.id)! : true,
      chats,
      children: [],
      groupHubId: hubId || undefined,
    });
  }

  const roots: FolderItem[] = [];
  for (const node of nodes.values()) {
    if (node.parentId && nodes.has(node.parentId)) {
      nodes.get(node.parentId)!.children.push(node);
    } else {
      node.parentId = "";
      roots.push(node);
    }
  }

  const sortFolderList = (list: FolderItem[]) => {
    list.sort((a, b) => a.sortOrder - b.sortOrder || a.name.localeCompare(b.name));
    for (const item of list) {
      item.chats.sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0) || a.name.localeCompare(b.name));
      sortFolderList(item.children);
    }
  };
  sortFolderList(roots);
  return roots;
}

export function flattenLayout(roots: FolderItem[], rootChats: FolderItem["chats"] = []): SidebarLayoutPatch {
  const folders: SidebarLayoutPatch["folders"] = [];
  const chats: SidebarLayoutPatch["chats"] = [];

  rootChats.forEach((chat, chatIndex) => {
    chat.sortOrder = chatIndex;
    chats.push({ id: chat.id, folder_id: "", sort_order: chatIndex });
  });

  const walkSiblings = (siblings: FolderItem[], parentId: string) => {
    siblings.forEach((folder, index) => {
      folder.parentId = parentId;
      folder.sortOrder = index;
      folders.push({ id: folder.id, parent_id: parentId, sort_order: index });
      folder.chats.forEach((chat, chatIndex) => {
        chat.sortOrder = chatIndex;
        chats.push({ id: chat.id, folder_id: folder.id, sort_order: chatIndex });
      });
      walkSiblings(folder.children, folder.id);
    });
  };

  walkSiblings(roots, "");
  return { folders, chats };
}

export function appendArchiveChatsToLayout(
  patch: SidebarLayoutPatch,
  archiveChats: FolderItem["chats"],
): SidebarLayoutPatch {
  const chats = [...patch.chats];
  archiveChats.forEach((chat, chatIndex) => {
    chat.sortOrder = chatIndex;
    chats.push({ id: chat.id, folder_id: ARCHIVE_FOLDER_ID, sort_order: chatIndex });
  });
  return { folders: patch.folders, chats };
}

export function flattenFoldersForSelect(roots: FolderItem[], depth = 0): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const folder of roots) {
    if (isArchiveFolderId(folder.id)) continue;
    const prefix = depth > 0 ? `${"— ".repeat(depth)}` : "";
    out.push({ id: folder.id, label: `${prefix}${folder.name}` });
    out.push(...flattenFoldersForSelect(folder.children, depth + 1));
  }
  return out;
}

function cloneTree(roots: FolderItem[]): FolderItem[] {
  return roots.map((folder) => ({
    ...folder,
    chats: folder.chats.map((chat) => ({ ...chat })),
    children: cloneTree(folder.children),
  }));
}

function findFolderParent(
  roots: FolderItem[],
  folderId: string,
  parent: FolderItem | null = null,
): { parent: FolderItem | null; list: FolderItem[]; index: number } | null {
  for (let i = 0; i < roots.length; i += 1) {
    if (roots[i].id === folderId) return { parent, list: roots, index: i };
    const nested = findFolderParent(roots[i].children, folderId, roots[i]);
    if (nested) return nested;
  }
  return null;
}

function findChat(
  roots: FolderItem[],
  rootChats: FolderItem["chats"],
  chatId: string,
): { list: FolderItem["chats"]; index: number } | null {
  const rootIdx = rootChats.findIndex((c) => c.id === chatId);
  if (rootIdx >= 0) return { list: rootChats, index: rootIdx };
  for (const folder of roots) {
    const idx = folder.chats.findIndex((c) => c.id === chatId);
    if (idx >= 0) return { list: folder.chats, index: idx };
    const nested = findChat(folder.children, [], chatId);
    if (nested) return nested;
  }
  return null;
}

function isDescendantFolder(roots: FolderItem[], ancestorId: string, candidateId: string): boolean {
  const ancestor = findFolderParent(roots, ancestorId);
  if (!ancestor) return false;
  const node = ancestor.list[ancestor.index];
  const walk = (folders: FolderItem[]): boolean => {
    for (const f of folders) {
      if (f.id === candidateId) return true;
      if (walk(f.children)) return true;
    }
    return false;
  };
  return walk(node.children);
}

function removeFolder(roots: FolderItem[], folderId: string): FolderItem | null {
  const loc = findFolderParent(roots, folderId);
  if (!loc) return null;
  const [removed] = loc.list.splice(loc.index, 1);
  return removed;
}

function removeChat(
  roots: FolderItem[],
  rootChats: FolderItem["chats"],
  chatId: string,
): { chat: FolderItem["chats"][number] } | null {
  const loc = findChat(roots, rootChats, chatId);
  if (!loc) return null;
  const [removed] = loc.list.splice(loc.index, 1);
  return { chat: removed };
}

function getFolderById(roots: FolderItem[], folderId: string): FolderItem | null {
  for (const folder of roots) {
    if (folder.id === folderId) return folder;
    const nested = getFolderById(folder.children, folderId);
    if (nested) return nested;
  }
  return null;
}

function findFolderByChatList(roots: FolderItem[], list: FolderItem["chats"]): FolderItem | null {
  for (const folder of roots) {
    if (folder.chats === list) return folder;
    const nested = findFolderByChatList(folder.children, list);
    if (nested) return nested;
  }
  return null;
}

export type DropPosition = "before" | "after" | "inside";

/** Map pointer-based drop hint to an over-id understood by applySidebarDrag. */
export function resolveDragOverId(
  roots: FolderItem[],
  rootChats: FolderItem["chats"],
  overRaw: string,
  position: DropPosition,
  activeRaw?: string,
): string {
  const active = activeRaw ? parseDragId(activeRaw) : null;
  const parsed = parseDragId(overRaw);
  if (!parsed) return overRaw;

  if (position === "inside") {
    if (parsed.kind === "folder") return nestDropId(parsed.id);
    return overRaw;
  }

  // Duckies sit above folders — before/after a folder with a chat = parent nest (not into it).
  if (active?.kind === "chat" && parsed.kind === "folder") {
    const loc = findFolderParent(roots, parsed.id);
    return nestDropId(loc?.parent?.id ?? "root");
  }

  // Folder dropped on a chat row → first sibling-folder slot in that parent.
  if (active?.kind === "folder" && parsed.kind === "chat") {
    const chatLoc = findChat(roots, rootChats, parsed.id);
    if (!chatLoc) return overRaw;
    if (chatLoc.list === rootChats) {
      if (roots.length > 0) return dragId("folder", roots[0].id);
      return nestDropId("root");
    }
    const owner = findFolderByChatList(roots, chatLoc.list);
    if (!owner) return nestDropId("root");
    if (owner.children.length > 0) return dragId("folder", owner.children[0].id);
    return nestDropId(owner.id);
  }

  if (position !== "after") return overRaw;

  if (parsed.kind === "folder") {
    const loc = findFolderParent(roots, parsed.id);
    if (!loc) return overRaw;
    const nextFolder = loc.list[loc.index + 1];
    if (nextFolder) return dragId("folder", nextFolder.id);
    return nestDropId(loc.parent?.id ?? "root");
  }

  const chatLoc = findChat(roots, rootChats, parsed.id);
  if (!chatLoc) return overRaw;
  const nextChat = chatLoc.list[chatLoc.index + 1];
  if (nextChat) return dragId("chat", nextChat.id);
  // Duckies render above folders (root + nested) — after last chat → first sibling folder.
  if (chatLoc.list === rootChats) {
    if (roots.length > 0) return dragId("folder", roots[0].id);
    return nestDropId("root");
  }
  const owner = findFolderByChatList(roots, chatLoc.list);
  if (!owner) return nestDropId("root");
  if (owner.children.length > 0) return dragId("folder", owner.children[0].id);
  return nestDropId(owner.id);
}

function findChatInArchive(
  archiveChats: FolderItem["chats"],
  chatId: string,
): { list: FolderItem["chats"]; index: number } | null {
  const index = archiveChats.findIndex((c) => c.id === chatId);
  if (index < 0) return null;
  return { list: archiveChats, index };
}

function removeChatEverywhere(
  roots: FolderItem[],
  rootChats: FolderItem["chats"],
  archiveChats: FolderItem["chats"],
  chatId: string,
): { chat: FolderItem["chats"][number] } | null {
  const archived = findChatInArchive(archiveChats, chatId);
  if (archived) {
    const [removed] = archived.list.splice(archived.index, 1);
    return { chat: removed };
  }
  return removeChat(roots, rootChats, chatId);
}

export type SidebarDragResult = {
  folders: FolderItem[];
  rootChats: FolderItem["chats"];
  archiveChats: FolderItem["chats"];
};

export function applySidebarDrag(
  roots: FolderItem[],
  rootChats: FolderItem["chats"],
  activeRaw: string,
  overRaw: string | null,
  archiveChats: FolderItem["chats"] = [],
): SidebarDragResult | null {
  if (!overRaw || activeRaw === overRaw) return null;

  const active = parseDragId(activeRaw);
  if (!active) return null;

  const nextRoots = cloneTree(roots);
  const nextRootChats = rootChats.map((c) => ({ ...c }));
  const nextArchiveChats = archiveChats.map((c) => ({ ...c }));

  const nestTarget = parseNestDropId(overRaw);
  if (nestTarget !== null) {
    if (isArchiveFolderId(nestTarget)) {
      if (active.kind !== "chat") return null;
      const removed = removeChatEverywhere(nextRoots, nextRootChats, nextArchiveChats, active.id);
      if (!removed) return null;
      nextArchiveChats.push(removed.chat);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }

    if (active.kind === "folder") {
      if (nestTarget && (nestTarget === active.id || isDescendantFolder(nextRoots, active.id, nestTarget))) {
        return null;
      }
      const folder = removeFolder(nextRoots, active.id);
      if (!folder) return null;
      const parentList = nestTarget ? getFolderById(nextRoots, nestTarget)?.children : nextRoots;
      if (!parentList) return null;
      folder.parentId = nestTarget;
      parentList.push(folder);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }

    const removed = removeChatEverywhere(nextRoots, nextRootChats, nextArchiveChats, active.id);
    if (!removed) return null;
    if (!nestTarget) {
      nextRootChats.push(removed.chat);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }
    const targetFolder = getFolderById(nextRoots, nestTarget);
    if (!targetFolder) return null;
    targetFolder.chats.push(removed.chat);
    return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
  }

  const over = parseDragId(overRaw);
  if (!over) return null;

  if (over.kind === "folder" && isArchiveFolderId(over.id)) {
    if (active.kind !== "chat") return null;
    const removed = removeChatEverywhere(nextRoots, nextRootChats, nextArchiveChats, active.id);
    if (!removed) return null;
    nextArchiveChats.push(removed.chat);
    return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
  }

  if (active.kind === "folder" && over.kind === "folder") {
    if (active.id === over.id) return null;
    if (isDescendantFolder(nextRoots, active.id, over.id)) return null;
    const activeLoc = findFolderParent(nextRoots, active.id);
    const overLoc = findFolderParent(nextRoots, over.id);
    if (!activeLoc || !overLoc) return null;
    const folder = removeFolder(nextRoots, active.id);
    if (!folder) return null;
    const sameParent = activeLoc.parent?.id === overLoc.parent?.id && activeLoc.list === overLoc.list;
    if (sameParent) {
      const insertAt = activeLoc.index < overLoc.index ? overLoc.index - 1 : overLoc.index;
      activeLoc.list.splice(insertAt, 0, folder);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }
    const parent = getFolderById(nextRoots, over.id);
    if (!parent) return null;
    folder.parentId = parent.id;
    parent.children.push(folder);
    parent.expanded = true;
    return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
  }

  const activeInArchive = active.kind === "chat" && findChatInArchive(nextArchiveChats, active.id);
  const overInArchive = over.kind === "chat" && findChatInArchive(nextArchiveChats, over.id);

  if (active.kind === "chat" && over.kind === "chat") {
    if (activeInArchive && overInArchive) {
      const list = nextArchiveChats;
      const [item] = list.splice(activeInArchive.index, 1);
      const insertAt = activeInArchive.index < overInArchive.index ? overInArchive.index - 1 : overInArchive.index;
      list.splice(insertAt, 0, item);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }
    const activeLoc = activeInArchive ?? findChat(nextRoots, nextRootChats, active.id);
    const overLoc = overInArchive ?? findChat(nextRoots, nextRootChats, over.id);
    if (!activeLoc || !overLoc) return null;
    if (activeLoc.list === overLoc.list) {
      const list = activeLoc.list;
      const [item] = list.splice(activeLoc.index, 1);
      const insertAt = activeLoc.index < overLoc.index ? overLoc.index - 1 : overLoc.index;
      list.splice(insertAt, 0, item);
      return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
    }
    const removed = removeChatEverywhere(nextRoots, nextRootChats, nextArchiveChats, active.id);
    if (!removed) return null;
    overLoc.list.splice(overLoc.index, 0, removed.chat);
    return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
  }

  if (active.kind === "chat" && over.kind === "folder") {
    const removed = removeChatEverywhere(nextRoots, nextRootChats, nextArchiveChats, active.id);
    const target = getFolderById(nextRoots, over.id);
    if (!removed || !target) return null;
    target.chats.push(removed.chat);
    return { folders: nextRoots, rootChats: nextRootChats, archiveChats: nextArchiveChats };
  }

  return null;
}

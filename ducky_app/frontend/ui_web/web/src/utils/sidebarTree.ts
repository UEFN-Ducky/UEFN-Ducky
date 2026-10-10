import type { FolderDto, FolderItem, SidebarLayoutPatch } from "../types/panel";
import { isArchiveFolderId } from "./archiveFolder";

export const PROJECT_FOLDER_PREFIX = "project:";

/** Chats with no island open. The Duckies tree shows these as Global Agents. */
export const GLOBAL_PROJECT_SLUG = "_no_project";

/** What the no-island bucket is called in the Duckies tree. */
export const GLOBAL_AGENTS_LABEL = "Global Agents";

export function isProjectFolderId(id: string): boolean {
  return id.startsWith(PROJECT_FOLDER_PREFIX);
}

export function projectFolderId(slug: string): string {
  return `${PROJECT_FOLDER_PREFIX}${slug}`;
}

export function isGlobalProjectFolderId(id: string): boolean {
  return id === projectFolderId(GLOBAL_PROJECT_SLUG);
}

/** Global Agents is drawn at the top of the tree, not in the project list. */
export function duckiesFoldersForDisplay(folders: FolderItem[]): FolderItem[] {
  return folders.filter((folder) => !isGlobalProjectFolderId(folder.id));
}

/** The Global Agents folder (the no-island bucket), when the tree has it. */
export function globalAgentsFolder(folders: FolderItem[]): FolderItem | null {
  return folders.find((folder) => isGlobalProjectFolderId(folder.id)) ?? null;
}

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

/** Ancestors the open chat may force open. Project rows stay where the user left them. */
export function foldersToAutoExpand(ancestorIds: readonly string[]): string[] {
  return ancestorIds.filter((id) => !isProjectFolderId(id));
}

function stampFolderProject(folders: FolderItem[], slug: string): FolderItem[] {
  return folders.map((folder) => ({
    ...folder,
    projectSlug: slug,
    chats: folder.chats.map((chat) => ({ ...chat, projectSlug: chat.projectSlug || slug })),
    children: stampFolderProject(folder.children, slug),
  }));
}

export function wrapProjectsAsFolders(
  projects: Array<{
    slug: string;
    name: string;
    folders: FolderItem[];
    rootChats: FolderItem["chats"];
  }>,
  currentSlug: string,
  expandedById: Map<string, boolean>,
): FolderItem[] {
  const ordered = [...projects].sort((a, b) => {
    const rank = (slug: string) => (slug === currentSlug ? 0 : slug === GLOBAL_PROJECT_SLUG ? 1 : 2);
    const byRank = rank(a.slug) - rank(b.slug);
    if (byRank) return byRank;
    return a.name.localeCompare(b.name) || a.slug.localeCompare(b.slug);
  });
  return ordered.map((project, index) => {
    const id = projectFolderId(project.slug);
    const expanded = expandedById.has(id)
      ? expandedById.get(id)!
      : project.slug === currentSlug || project.slug === GLOBAL_PROJECT_SLUG;
    return {
      id,
      name: project.name,
      parentId: "",
      sortOrder: index,
      expanded,
      chats: project.rootChats.map((chat) => ({
        ...chat,
        projectSlug: chat.projectSlug || project.slug,
      })),
      children: stampFolderProject(project.folders, project.slug),
      projectSlug: project.slug,
    };
  });
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
  if (!parentId) return folders.filter((f) => !isProjectFolderId(f.id)).map((f) => f.name);
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
    parentId: isProjectFolderId(parent) ? "" : parent,
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
  const own = folders.filter((f) => !isProjectFolderId(f.id));
  const wrappers = folders.filter((f) => isProjectFolderId(f.id));
  return [...own, asChildOf("", own), ...wrappers];
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

/** One project's order and nesting, as saved by apply_sidebar_layout. Project rows
 *  themselves are never part of it. */
export function flattenLayout(roots: FolderItem[], rootChats: FolderItem["chats"] = []): SidebarLayoutPatch {
  const folders: SidebarLayoutPatch["folders"] = [];
  const chats: SidebarLayoutPatch["chats"] = [];

  rootChats.forEach((chat, chatIndex) => {
    chat.sortOrder = chatIndex;
    chats.push({ id: chat.id, folder_id: "", sort_order: chatIndex });
  });

  const walkSiblings = (siblings: FolderItem[], parentId: string) => {
    siblings.forEach((folder, index) => {
      if (isProjectFolderId(folder.id)) return;
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

export function flattenFoldersForSelect(roots: FolderItem[], depth = 0): { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const folder of roots) {
    if (isArchiveFolderId(folder.id)) continue;
    if (isProjectFolderId(folder.id)) {
      out.push(...flattenFoldersForSelect(folder.children, depth));
      continue;
    }
    const prefix = depth > 0 ? `${"— ".repeat(depth)}` : "";
    out.push({ id: folder.id, label: `${prefix}${folder.name}` });
    out.push(...flattenFoldersForSelect(folder.children, depth + 1));
  }
  return out;
}

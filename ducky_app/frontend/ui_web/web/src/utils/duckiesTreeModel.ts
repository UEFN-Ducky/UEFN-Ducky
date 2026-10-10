/**
 * The Duckies tree as a shared tree model (see tree-dnd/treeMove): every project row,
 * Global Agents, folders, groups and chats. Moves, undo and the saved layout all go
 * through here so a group always moves with its members and nothing can nest into itself.
 */
import type { FolderItem, SidebarLayoutPatch } from "../types/panel";
import {
  buildTreeModel,
  childIds,
  moveNodes,
  restorePlaces,
  TREE_ROOT,
  type DropPolicy,
  type DropTarget,
  type TreeEntry,
  type TreeModel,
} from "../tree-dnd/treeMove";
import {
  dragId,
  findFolderById,
  flattenLayout,
  GLOBAL_PROJECT_SLUG,
  isProjectFolderId,
  parseDragId,
  projectFolderId,
  PROJECT_FOLDER_PREFIX,
} from "./sidebarTree";

type Chat = FolderItem["chats"][number];

export interface DuckiesTreeData {
  folders: FolderItem[];
  rootChats: Chat[];
}

export interface DuckiesTreeIndex {
  model: TreeModel;
  currentSlug: string;
  /** Where a drop on blank space lands: the open project's own root. */
  currentRootId: string;
  /** One-project view: the open project's chats and folders sit at the top level, with
   *  no project row; the model wraps them in this synthetic root. */
  syntheticCurrent: boolean;
  /** Node id → folder (project rows, Global Agents, folders, groups). */
  folders: Map<string, FolderItem>;
  /** Node id → chat. */
  chats: Map<string, Chat>;
}

export type DuckiesLayoutPatch = SidebarLayoutPatch & { project_slug: string };

/** Node id of a project row (Global Agents is the `_no_project` one). */
export function projectNodeId(slug: string): string {
  return dragId("folder", projectFolderId(slug));
}

export function isProjectNode(nodeId: string): boolean {
  const parsed = parseDragId(nodeId);
  return parsed?.kind === "folder" && isProjectFolderId(parsed.id);
}

export function buildDuckiesIndex(data: DuckiesTreeData, currentSlug: string): DuckiesTreeIndex {
  const entries: TreeEntry[] = [];
  const folders = new Map<string, FolderItem>();
  const chats = new Map<string, Chat>();
  const walkFolder = (folder: FolderItem, parentId: string, depth: number) => {
    const nodeId = dragId("folder", folder.id);
    entries.push({ id: nodeId, parentId, branch: true });
    folders.set(nodeId, folder);
    // Chats render above folders inside every folder.
    for (const chat of folder.chats) {
      const chatId = dragId("chat", chat.id);
      entries.push({ id: chatId, parentId: nodeId, branch: false });
      chats.set(chatId, chat);
    }
    if (depth > 64) return;
    for (const child of folder.children) walkFolder(child, nodeId, depth + 1);
  };

  const wrappers = data.folders.filter((folder) => isProjectFolderId(folder.id));
  const own = data.folders.filter((folder) => !isProjectFolderId(folder.id));
  const currentWrapper = wrappers.find((folder) => folder.id === projectFolderId(currentSlug));
  const syntheticCurrent = !currentWrapper;
  const currentRootId = currentWrapper ? dragId("folder", currentWrapper.id) : projectNodeId(currentSlug);

  // Display order: Global Agents first, then the open project, then the other islands.
  const global = wrappers.find((folder) => folder.id === projectFolderId(GLOBAL_PROJECT_SLUG));
  if (global) walkFolder(global, TREE_ROOT, 0);
  if (syntheticCurrent) {
    entries.push({ id: currentRootId, parentId: TREE_ROOT, branch: true });
    for (const chat of data.rootChats) {
      const chatId = dragId("chat", chat.id);
      entries.push({ id: chatId, parentId: currentRootId, branch: false });
      chats.set(chatId, chat);
    }
    for (const folder of own) walkFolder(folder, currentRootId, 1);
  }
  for (const wrapper of wrappers) {
    if (wrapper === global) continue;
    walkFolder(wrapper, TREE_ROOT, 0);
  }
  return {
    model: buildTreeModel(entries),
    currentSlug,
    currentRootId,
    syntheticCurrent,
    folders,
    chats,
  };
}

/** Duckies keep the order people give them; chats sit above folders; project rows
 *  (and Global Agents) only take drops into them. */
export function duckiesDropPolicy(index: DuckiesTreeIndex, filtering = false): DropPolicy {
  return {
    ordered: true,
    leavesFirst: true,
    isExpanded: (id) => {
      if (filtering) return true;
      if (index.syntheticCurrent && id === index.currentRootId) return true;
      return index.folders.get(id)?.expanded ?? false;
    },
    intoOnly: (id) => isProjectNode(id),
    acceptsDrop: (parentId) => parentId !== TREE_ROOT,
    blankParent: index.currentRootId,
  };
}

/** Project and folder a tree parent stands for ("" folder = the project's root). */
export function duckiesPlace(index: DuckiesTreeIndex, parentNodeId: string): { slug: string; folderId: string } {
  const parsed = parseDragId(parentNodeId);
  const id = parsed?.id ?? "";
  if (isProjectFolderId(id)) return { slug: id.slice(PROJECT_FOLDER_PREFIX.length), folderId: "" };
  const folder = index.folders.get(parentNodeId);
  return { slug: folder?.projectSlug || index.currentSlug, folderId: id };
}

/** The project a chat or folder lives in. */
export function duckiesNodeSlug(index: DuckiesTreeIndex, nodeId: string): string {
  const chat = index.chats.get(nodeId);
  if (chat) return chat.projectSlug || index.currentSlug;
  return index.folders.get(nodeId)?.projectSlug || index.currentSlug;
}

function stampProject(folder: FolderItem, slug: string): FolderItem {
  return {
    ...folder,
    projectSlug: slug,
    chats: folder.chats.map((chat) => ({ ...chat, projectSlug: slug })),
    children: folder.children.map((child) => stampProject(child, slug)),
  };
}

/** Turn a moved model back into folders and root chats (same shapes as the loader). */
export function rebuildDuckiesTree(
  index: DuckiesTreeIndex,
  model: TreeModel,
  data: DuckiesTreeData,
  restamp: ReadonlyMap<string, string> = new Map(),
): DuckiesTreeData {
  const chatOf = (nodeId: string): Chat => {
    const chat = { ...index.chats.get(nodeId)! };
    const slug = restamp.get(nodeId);
    return slug ? { ...chat, projectSlug: slug } : chat;
  };
  const buildFolder = (nodeId: string, parentId: string, depth: number): FolderItem => {
    const base = index.folders.get(nodeId)!;
    const kids = depth > 64 ? [] : childIds(model, nodeId);
    const own = isProjectFolderId(base.id) ? "" : base.id;
    const built: FolderItem = {
      ...base,
      parentId,
      chats: kids.filter((kid) => index.chats.has(kid)).map(chatOf),
      children: kids.filter((kid) => index.folders.has(kid)).map((kid) => buildFolder(kid, own, depth + 1)),
    };
    const slug = restamp.get(nodeId);
    return slug ? stampProject(built, slug) : built;
  };
  const wrappers = data.folders
    .filter((folder) => isProjectFolderId(folder.id))
    .map((folder) => buildFolder(dragId("folder", folder.id), "", 0));
  if (!index.syntheticCurrent || !model.parent.has(index.currentRootId)) {
    return { folders: wrappers, rootChats: data.rootChats.map((chat) => ({ ...chat })) };
  }
  const kids = childIds(model, index.currentRootId);
  return {
    folders: [
      ...kids.filter((kid) => index.folders.has(kid)).map((kid) => buildFolder(kid, "", 1)),
      ...wrappers,
    ],
    rootChats: kids.filter((kid) => index.chats.has(kid)).map(chatOf),
  };
}

/** The saved layout for one project, or null when this view does not show it. */
export function duckiesLayoutFor(data: DuckiesTreeData, slug: string, currentSlug: string): DuckiesLayoutPatch | null {
  const wrapper = data.folders.find((folder) => folder.id === projectFolderId(slug));
  if (wrapper) return { ...flattenLayout(wrapper.children, wrapper.chats), project_slug: slug };
  if (slug !== currentSlug) return null;
  const own = data.folders.filter((folder) => !isProjectFolderId(folder.id));
  return { ...flattenLayout(own, data.rootChats), project_slug: slug };
}

export interface DuckiesMovePlan {
  /** The tree right after the move (shown at once, before the host answers). */
  data: DuckiesTreeData;
  /** Projects whose order and nesting to save. */
  layoutSlugs: string[];
  /** Chats and folders that change project; the host relocates them first. */
  projectMoves: Array<{ slug: string; folderId: string; convIds: string[]; folderIds: string[] }>;
}

function splitIds(nodeIds: readonly string[]): { convIds: string[]; folderIds: string[] } {
  const convIds: string[] = [];
  const folderIds: string[] = [];
  for (const nodeId of nodeIds) {
    const parsed = parseDragId(nodeId);
    if (!parsed || isProjectFolderId(parsed.id)) continue;
    if (parsed.kind === "chat") convIds.push(parsed.id);
    else folderIds.push(parsed.id);
  }
  return { convIds, folderIds };
}

/** Plan a drop: one optimistic tree, the project moves, and the layouts to save. */
export function planDuckiesDrop(
  index: DuckiesTreeIndex,
  data: DuckiesTreeData,
  sources: readonly string[],
  target: DropTarget,
  policy: DropPolicy,
): DuckiesMovePlan {
  const dest = duckiesPlace(index, target.parentId);
  const foreign = sources.filter((source) => duckiesNodeSlug(index, source) !== dest.slug);
  const restamp = new Map(foreign.map((source) => [source, dest.slug]));
  const model = moveNodes(index.model, sources, target, policy);
  const projectMoves = foreign.length ? [{ slug: dest.slug, folderId: dest.folderId, ...splitIds(foreign) }] : [];
  return { data: rebuildDuckiesTree(index, model, data, restamp), layoutSlugs: [dest.slug], projectMoves };
}

export interface DuckiesPlace {
  id: string;
  parentId: string;
  index: number;
  slug: string;
  folderId: string;
}

/** Where these nodes sit now, to put them back on undo. */
export function duckiesPlaces(index: DuckiesTreeIndex, sources: readonly string[]): DuckiesPlace[] {
  return sources
    .map((id) => {
      const parentId = index.model.parent.get(id);
      if (parentId === undefined) return null;
      const place = duckiesPlace(index, parentId);
      return { id, parentId, index: childIds(index.model, parentId).indexOf(id), ...place };
    })
    .filter((place): place is DuckiesPlace => place !== null)
    .sort((a, b) => a.index - b.index);
}

/** Plan putting nodes back where `duckiesPlaces` saw them (the undo of a move). */
export function planDuckiesRestore(
  index: DuckiesTreeIndex,
  data: DuckiesTreeData,
  places: readonly DuckiesPlace[],
): DuckiesMovePlan {
  const restamp = new Map<string, string>();
  const groups = new Map<string, { slug: string; folderId: string; ids: string[] }>();
  for (const place of places) {
    if (!index.model.parent.has(place.id)) continue;
    if (duckiesNodeSlug(index, place.id) === place.slug) continue;
    restamp.set(place.id, place.slug);
    const key = `${place.slug}\u0000${place.folderId}`;
    const group = groups.get(key) ?? { slug: place.slug, folderId: place.folderId, ids: [] };
    group.ids.push(place.id);
    groups.set(key, group);
  }
  const model = restorePlaces(index.model, places);
  return {
    data: rebuildDuckiesTree(index, model, data, restamp),
    layoutSlugs: [...new Set(places.map((place) => place.slug))],
    projectMoves: [...groups.values()].map((group) => ({ slug: group.slug, folderId: group.folderId, ...splitIds(group.ids) })),
  };
}

/** Rows gone from the tree right away (deleted here or in another window). Contents of a
 *  removed folder move up to its parent unless `keepContents` is false. */
export function removeFromDuckies(
  data: DuckiesTreeData,
  removal: { chatIds?: readonly string[]; folderIds?: readonly string[]; keepContents?: boolean },
): DuckiesTreeData {
  const chatsGone = new Set(removal.chatIds ?? []);
  const foldersGone = new Set(removal.folderIds ?? []);
  if (!chatsGone.size && !foldersGone.size) return data;
  const keep = removal.keepContents !== false;
  const walk = (list: FolderItem[], depth: number): { kept: FolderItem[]; chats: Chat[]; folders: FolderItem[] } => {
    const kept: FolderItem[] = [];
    const liftedChats: Chat[] = [];
    const liftedFolders: FolderItem[] = [];
    for (const folder of list) {
      const inner = depth > 64 ? { kept: [], chats: [], folders: [] } : walk(folder.children, depth + 1);
      const chats = [...folder.chats.filter((chat) => !chatsGone.has(chat.id)), ...inner.chats];
      const children = [...inner.kept, ...inner.folders];
      if (foldersGone.has(folder.id)) {
        if (keep) {
          liftedChats.push(...chats);
          liftedFolders.push(...children);
        }
        continue;
      }
      kept.push({ ...folder, chats, children });
    }
    return { kept, chats: liftedChats, folders: liftedFolders };
  };
  const top = walk(data.folders, 0);
  return {
    folders: [...top.kept, ...top.folders],
    rootChats: [...data.rootChats.filter((chat) => !chatsGone.has(chat.id)), ...top.chats],
  };
}

/** Where "new ducky" / "new group" in this folder goes: its folder, and its project
 *  when that is not the open one (Global Agents, another island). */
export function duckiesCreateTarget(
  folderId: string,
  currentSlug: string,
  folders: FolderItem[],
): { folderId: string; projectSlug?: string } {
  if (!folderId) return { folderId: "" };
  if (isProjectFolderId(folderId)) {
    const slug = folderId.slice(PROJECT_FOLDER_PREFIX.length);
    return slug && slug !== currentSlug ? { folderId: "", projectSlug: slug } : { folderId: "" };
  }
  const folder = findFolderById(folders, folderId);
  if (!folder) return { folderId: "" };
  if (folder.projectSlug && folder.projectSlug !== currentSlug) return { folderId, projectSlug: folder.projectSlug };
  return { folderId };
}

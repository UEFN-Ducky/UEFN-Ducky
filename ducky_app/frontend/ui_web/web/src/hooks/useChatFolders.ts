import { useCallback, useEffect, useRef, useState } from "react";
import type { FolderDto, FolderItem } from "../types/panel";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { ARCHIVE_FOLDER_ID, isArchiveFolderId } from "../utils/archiveFolder";
import { buildFolderTree } from "../utils/sidebarTree";
import { saveFolderOpenState } from "../utils/folderOpenState";
import { duckiesLayoutHold } from "../utils/duckiesLayoutHold";
import { removeFromDuckies } from "../utils/duckiesTreeModel";

type ConvRow = Awaited<ReturnType<NonNullable<ReturnType<typeof getApi>>["list_all_conversations"]>>[number];

function mapConversations(convs: ConvRow[]): FolderItem["chats"] {
  const leaderByHub = new Map<string, string>();
  for (const c of convs) {
    if (c.is_group) {
      const lid = String((c as { leader_conv_id?: string }).leader_conv_id || "").trim();
      if (lid) leaderByHub.set(c.id, lid);
    }
  }
  return convs.map((c) => {
    const parentId = c.parent_conv_id?.trim() || undefined;
    const leaderConvId = String((c as { leader_conv_id?: string }).leader_conv_id || "").trim() || undefined;
    const isLeader = Boolean(parentId && leaderByHub.get(parentId) === c.id);
    const projectSlug = String((c as { project_slug?: string }).project_slug || "").trim() || undefined;
    const projectName = String((c as { project_name?: string }).project_name || "").trim() || undefined;
    return {
      id: c.id,
      name: c.title,
      duckyStyle: c.ducky_style || undefined,
      duckyName: c.ducky_name || undefined,
      profileId: (c as { profile_id?: string }).profile_id?.trim() || undefined,
      duckyPersonality: c.ducky_personality || undefined,
      ttsVoice: c.tts_voice || undefined,
      ttsSpeed: Number(c.tts_speed) || undefined,
      sortOrder: Number(c.sort_order) || 0,
      updated: Number(c.updated) || 0,
      filePath: c.file_path?.replace(/\\/g, "/") || undefined,
      model: c.model?.trim() || undefined,
      provider: c.provider?.trim() || undefined,
      codingAgent: c.coding_agent?.trim() || "ducky",
      thinkingEffort: c.thinking_effort?.trim() || undefined,
      terminalSessionId: c.terminal_session_id?.trim() || undefined,
      parentConvId: parentId,
      isGroup: Boolean(c.is_group),
      isSubagent: false,
      leaderConvId,
      isLeader,
      groupMembers: Array.isArray(c.group_members) ? c.group_members : undefined,
      toolCallCount: Number(c.tool_call_count) || 0,
      fileCount: Number(c.file_count) || 0,
      contextTokens: Number(c.context_tokens) || 0,
      projectSlug,
      projectName,
    };
  });
}

function collectExpanded(items: FolderItem[], into: Map<string, boolean>) {
  for (const item of items) {
    into.set(item.id, item.expanded);
    collectExpanded(item.children, into);
  }
}

function assembleOneProject(
  folderRows: FolderDto[],
  allConvs: ConvRow[],
  expandedById: Map<string, boolean>,
): {
  folders: FolderItem[];
  rootChats: FolderItem["chats"];
  hubChats: FolderItem["chats"];
  archiveChats: FolderItem["chats"];
} {
  const chatsByFolder = new Map<string, FolderItem["chats"]>();
  const archive: ConvRow[] = [];
  const byFolder = new Map<string, ConvRow[]>();
  const validFolderIds = new Set(folderRows.map((f) => f.id));

  for (const c of allConvs) {
    const storedFid = (c.folder_id || "").trim();
    if (isArchiveFolderId(storedFid) || storedFid === ARCHIVE_FOLDER_ID) {
      archive.push(c);
      continue;
    }
    const fid = storedFid && validFolderIds.has(storedFid) ? storedFid : "";
    const bucket = byFolder.get(fid) ?? [];
    bucket.push(c);
    byFolder.set(fid, bucket);
  }

  for (const [fid, rows] of byFolder) {
    chatsByFolder.set(fid, mapConversations(rows));
  }
  for (const f of folderRows) {
    if (!chatsByFolder.has(f.id)) chatsByFolder.set(f.id, []);
  }
  if (!chatsByFolder.has("")) chatsByFolder.set("", []);

  const hubIds = new Set(
    folderRows.map((f) => String(f.group_hub_id || "").trim()).filter(Boolean),
  );
  const hubs: FolderItem["chats"] = [];
  for (const rows of chatsByFolder.values()) {
    for (const c of rows) {
      if (c.isGroup && hubIds.has(c.id)) hubs.push(c);
    }
  }

  // Top-level rows show in saved order (a reorder must show as saved). Ties keep host order.
  const rootRaw = [...(chatsByFolder.get("") ?? [])].sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0));
  return {
    folders: buildFolderTree(folderRows, chatsByFolder, expandedById),
    rootChats: rootRaw.filter((c) => !(c.isGroup && hubIds.has(c.id))),
    hubChats: hubs,
    archiveChats: mapConversations(archive),
  };
}

const isPlain = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && Object.getPrototypeOf(v) === Object.prototype;
const idOf = (v: unknown): unknown => (isPlain(v) && typeof v.id === "string" ? v.id : undefined);

/** `next` with every part equal to `prev` swapped for the `prev` object (array items
 *  matched by `id`), so a reload with no change returns `prev` itself and one renamed
 *  row leaves the other rows' objects (and the panes memoized on them) as they were. */
export function shareRows<T>(prev: T, next: T): T {
  if (Object.is(prev, next)) return prev;
  if (Array.isArray(prev) && Array.isArray(next)) {
    const byKey = new Map<unknown, unknown>();
    prev.forEach((p, i) => byKey.set(idOf(p) ?? i, p));
    let same = prev.length === next.length;
    const out = next.map((n, i) => {
      const p = byKey.get(idOf(n) ?? i);
      const s = p === undefined ? n : shareRows(p, n);
      if (s !== prev[i]) same = false;
      return s;
    });
    return (same ? prev : out) as T;
  }
  if (isPlain(prev) && isPlain(next)) {
    const keys = Object.keys(next);
    let same = keys.length === Object.keys(prev).length;
    const out: Record<string, unknown> = {};
    for (const k of keys) {
      const s = shareRows(prev[k], next[k]);
      if (s !== prev[k] || !(k in prev)) same = false;
      out[k] = s;
    }
    return (same ? prev : out) as T;
  }
  return next;
}

export function useChatFolders(refreshToken: number) {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [rootChats, setRootChats] = useState<FolderItem["chats"]>([]);
  /** Group hub chats hidden from the sidebar tree but still needed for ChatPane lookup. */
  const [hubChats, setHubChats] = useState<FolderItem["chats"]>([]);
  const [archiveChats, setArchiveChats] = useState<FolderItem["chats"]>([]);
  const [foldersLoaded, setFoldersLoaded] = useState(false);
  const foldersRef = useRef(folders);
  foldersRef.current = folders;
  // Every expand/collapse goes through setFolders, so this keeps it across reloads.
  useEffect(() => saveFolderOpenState(folders), [folders]);
  const rootChatsRef = useRef(rootChats);
  rootChatsRef.current = rootChats;
  const hubChatsRef = useRef(hubChats);
  hubChatsRef.current = hubChats;
  const archiveChatsRef = useRef(archiveChats);
  archiveChatsRef.current = archiveChats;
  // Only the newest load paints, and never while a move or delete is being saved:
  // a slow, older answer must not put back a row that was just moved or deleted.
  const loadSeqRef = useRef(0);

  const load = useCallback(async () => {
    const api = getApi();
    // A bridge still starting is not an empty library. onApiReady retries the load.
    if (!api) return;
    const seq = ++loadSeqRef.current;
    const epoch = duckiesLayoutHold.epoch();
    const stale = () => seq !== loadSeqRef.current || duckiesLayoutHold.held() || duckiesLayoutHold.epoch() !== epoch;
    // Every ducky is one list: no project rows and no Global Agents header, just the
    // duckies, groups and folders people made. The selected project only drives Content.
    const [folderRows, allConvs] = await Promise.all([
      api.list_folders().then((rows) => (Array.isArray(rows) ? rows : []).filter((f) => !isArchiveFolderId(f.id))),
      api.list_all_conversations().then((rows) => (Array.isArray(rows) ? rows : [])),
    ]);

    const expandedById = new Map<string, boolean>();
    collectExpanded(foldersRef.current, expandedById);
    const one = assembleOneProject(folderRows, allConvs, expandedById);
    if (stale()) return;
    // Unchanged rows keep their objects; an unchanged list is not set at all.
    const archive = shareRows(archiveChatsRef.current, one.archiveChats);
    const hubs = shareRows(hubChatsRef.current, one.hubChats);
    const root = shareRows(rootChatsRef.current, one.rootChats);
    const tree = shareRows(foldersRef.current, one.folders);
    if (archive !== archiveChatsRef.current) setArchiveChats(archive);
    if (hubs !== hubChatsRef.current) setHubChats(hubs);
    if (root !== rootChatsRef.current) setRootChats(root);
    if (tree !== foldersRef.current) setFolders(tree);
    setFoldersLoaded(true);
  }, []);

  useEffect(() => {
    setFoldersLoaded(false);
    return onApiReady(() => { void load(); });
  }, [load, refreshToken]);

  // A saved move or delete releases the hold: show what the host has now.
  useEffect(() => duckiesLayoutHold.onReleased(() => { void load(); }), [load]);

  /** Rows deleted here or in another window leave the tree at once (load() follows). */
  const removeRows = useCallback((convIds: readonly string[], folderIds: readonly string[]) => {
    if (!convIds.length && !folderIds.length) return;
    loadSeqRef.current += 1; // an answer already on its way still has them
    const next = removeFromDuckies(
      { folders: foldersRef.current, rootChats: rootChatsRef.current },
      { chatIds: convIds, folderIds },
    );
    foldersRef.current = next.folders;
    rootChatsRef.current = next.rootChats;
    setFolders(next.folders);
    setRootChats(next.rootChats);
    setHubChats((prev) => prev.filter((chat) => !convIds.includes(chat.id)));
  }, []);

  return {
    folders,
    setFolders,
    rootChats,
    setRootChats,
    hubChats,
    archiveChats,
    setArchiveChats,
    load,
    foldersLoaded,
    removeRows,
  };
}

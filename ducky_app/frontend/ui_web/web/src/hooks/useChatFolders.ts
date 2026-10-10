import { useCallback, useEffect, useRef, useState } from "react";
import type { FolderDto, FolderItem } from "../types/panel";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { ARCHIVE_FOLDER_ID, isArchiveFolderId } from "../utils/archiveFolder";
import { readDuckiesAllProjects } from "../utils/duckiesTreePrefs";
import { buildFolderTree, GLOBAL_PROJECT_SLUG, wrapProjectsAsFolders } from "../utils/sidebarTree";
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

  const rootRaw = chatsByFolder.get("") ?? [];
  return {
    folders: buildFolderTree(folderRows, chatsByFolder, expandedById),
    rootChats: rootRaw.filter((c) => !(c.isGroup && hubIds.has(c.id))),
    hubChats: hubs,
    archiveChats: mapConversations(archive),
  };
}

export function useChatFolders(refreshToken: number, currentProjectSlug = "") {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [rootChats, setRootChats] = useState<FolderItem["chats"]>([]);
  /** Group hub chats hidden from the sidebar tree but still needed for ChatPane lookup. */
  const [hubChats, setHubChats] = useState<FolderItem["chats"]>([]);
  const [archiveChats, setArchiveChats] = useState<FolderItem["chats"]>([]);
  const [foldersLoaded, setFoldersLoaded] = useState(false);
  const foldersRef = useRef(folders);
  foldersRef.current = folders;
  const rootChatsRef = useRef(rootChats);
  rootChatsRef.current = rootChats;
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
    const allProjects = readDuckiesAllProjects();
    const [folderRows, allConvs] = await Promise.all([
      api.list_folders(allProjects).then((rows) => (Array.isArray(rows) ? rows : []).filter((f) => !isArchiveFolderId(f.id))),
      api.list_all_conversations(allProjects).then((rows) => (Array.isArray(rows) ? rows : [])),
    ]);

    const expandedById = new Map<string, boolean>();
    collectExpanded(foldersRef.current, expandedById);

    const archiveSource = allProjects
      ? allConvs
      : await api.list_all_conversations(true).then((rows) => (Array.isArray(rows) ? rows : [])).catch(() => [] as ConvRow[]);
    const archivedRows = archiveSource.filter((c) => {
      const storedFid = (c.folder_id || "").trim();
      return isArchiveFolderId(storedFid) || storedFid === ARCHIVE_FOLDER_ID;
    });
    const everyArchive = mapConversations(archivedRows);

    if (!allProjects) {
      if (stale()) return;
      // Duckies with no island come tagged "_no_project": they are the Global Agents
      // folder, a real folder shown on every island (with no island open it holds all).
      const currentIsGlobal = currentProjectSlug === GLOBAL_PROJECT_SLUG;
      const isGlobal = (slug: unknown) => currentIsGlobal || String(slug || "").trim() === GLOBAL_PROJECT_SLUG;
      const ownFolders = folderRows.filter((row) => !isGlobal(row.project_slug));
      const ownConvs = allConvs.filter((conv) => !isGlobal((conv as { project_slug?: string }).project_slug));
      const globalFolderRows = folderRows.filter((row) => isGlobal(row.project_slug));
      const globalConvs = allConvs.filter((conv) => isGlobal((conv as { project_slug?: string }).project_slug));
      const one = assembleOneProject(ownFolders, ownConvs, expandedById);
      // Always there (even empty) so duckies and groups can be dropped into it.
      const global = assembleOneProject(globalFolderRows, globalConvs, expandedById);
      const globalWrap = wrapProjectsAsFolders(
        [{ slug: GLOBAL_PROJECT_SLUG, name: "No project", folders: global.folders, rootChats: global.rootChats }],
        currentProjectSlug,
        expandedById,
      );
      setArchiveChats(everyArchive);
      setHubChats([...one.hubChats, ...global.hubChats]);
      setRootChats(one.rootChats);
      setFolders([...one.folders, ...globalWrap]);
      setFoldersLoaded(true);
      return;
    }

    type Group = { name: string; folderRows: FolderDto[]; convs: ConvRow[] };
    const bySlug = new Map<string, Group>();
    const ensure = (slug: string, name: string): Group => {
      const existing = bySlug.get(slug);
      if (existing) {
        if (name && existing.name === slug) existing.name = name;
        return existing;
      }
      const created: Group = { name: name || slug, folderRows: [], convs: [] };
      bySlug.set(slug, created);
      return created;
    };

    for (const row of folderRows) {
      const slug = String(row.project_slug || "").trim();
      if (!slug) continue;
      ensure(slug, String(row.project_name || "").trim()).folderRows.push(row);
    }
    for (const conv of allConvs) {
      const slug = String((conv as { project_slug?: string }).project_slug || "").trim();
      if (!slug) continue;
      ensure(slug, String((conv as { project_name?: string }).project_name || "").trim()).convs.push(conv);
    }
    const recents = api.list_recent_projects
      ? await api.list_recent_projects().catch(() => [])
      : [];
    for (const recent of recents) {
      const slug = String(recent.slug || "").trim();
      if (!slug) continue;
      ensure(slug, String(recent.name || "").trim());
    }
    if (currentProjectSlug) ensure(currentProjectSlug, "");
    ensure(GLOBAL_PROJECT_SLUG, "No project");

    const projects: Array<{
      slug: string;
      name: string;
      folders: FolderItem[];
      rootChats: FolderItem["chats"];
      hubChats: FolderItem["chats"];
      archiveChats: FolderItem["chats"];
    }> = [];
    for (const [slug, group] of bySlug) {
      const one = assembleOneProject(group.folderRows, group.convs, expandedById);
      projects.push({ slug, name: group.name, ...one });
    }

    if (stale()) return;
    setHubChats(projects.flatMap((p) => p.hubChats));
    setArchiveChats(everyArchive);
    setRootChats([]);
    setFolders(wrapProjectsAsFolders(projects, currentProjectSlug, expandedById));
    setFoldersLoaded(true);
  }, [currentProjectSlug]);

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

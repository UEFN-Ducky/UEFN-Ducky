import { useCallback, useEffect, useRef, useState } from "react";
import type { FolderDto, FolderItem } from "../types/panel";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { ARCHIVE_FOLDER_ID, isArchiveFolderId } from "../utils/archiveFolder";
import { readDuckiesAllProjects } from "../utils/duckiesTreePrefs";
import { buildFolderTree, wrapProjectsAsFolders } from "../utils/sidebarTree";

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

  const load = useCallback(async () => {
    const api = getApi();
    // A bridge still starting is not an empty library. onApiReady retries the load.
    if (!api) return;
    const allProjects = readDuckiesAllProjects();
    const [folderRows, allConvs] = await Promise.all([
      api.list_folders(allProjects).then((rows) => (Array.isArray(rows) ? rows : []).filter((f) => !isArchiveFolderId(f.id))),
      api.list_all_conversations(allProjects).then((rows) => (Array.isArray(rows) ? rows : [])),
    ]);

    const expandedById = new Map<string, boolean>();
    collectExpanded(foldersRef.current, expandedById);

    if (!allProjects) {
      const one = assembleOneProject(folderRows, allConvs, expandedById);
      setArchiveChats(one.archiveChats);
      setHubChats(one.hubChats);
      setRootChats(one.rootChats);
      setFolders(one.folders);
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

    const projects: Array<{
      slug: string;
      name: string;
      folders: FolderItem[];
      rootChats: FolderItem["chats"];
      hubChats: FolderItem["chats"];
      archiveChats: FolderItem["chats"];
    }> = [];
    for (const [slug, group] of bySlug) {
      if (group.convs.length === 0) continue;
      const one = assembleOneProject(group.folderRows, group.convs, expandedById);
      if (one.rootChats.length === 0 && one.folders.length === 0) continue;
      projects.push({ slug, name: group.name, ...one });
    }

    setHubChats(projects.flatMap((p) => p.hubChats));
    setArchiveChats(projects.find((p) => p.slug === currentProjectSlug)?.archiveChats ?? []);
    setRootChats([]);
    setFolders(wrapProjectsAsFolders(projects, currentProjectSlug, expandedById));
    setFoldersLoaded(true);
  }, [currentProjectSlug]);

  useEffect(() => {
    setFoldersLoaded(false);
    return onApiReady(() => { void load(); });
  }, [load, refreshToken]);

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
  };
}

import { useCallback, useEffect, useState } from "react";
import type { FolderItem } from "../types/panel";
import { getApi } from "./usePanelApi";
import { ARCHIVE_FOLDER_ID, isArchiveFolderId } from "../utils/archiveFolder";
import { buildFolderTree } from "../utils/sidebarTree";

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
    };
  });
}

export function useChatFolders(refreshToken: number) {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [rootChats, setRootChats] = useState<FolderItem["chats"]>([]);
  /** Group hub chats hidden from the sidebar tree but still needed for ChatPane lookup. */
  const [hubChats, setHubChats] = useState<FolderItem["chats"]>([]);
  const [archiveChats, setArchiveChats] = useState<FolderItem["chats"]>([]);
  const [foldersLoaded, setFoldersLoaded] = useState(false);

  const load = useCallback(async () => {
    const api = getApi();
    if (!api) {
      setFolders([]);
      setRootChats([]);
      setHubChats([]);
      setArchiveChats([]);
      setFoldersLoaded(true);
      return;
    }
    const [folderRows, allConvs] = await Promise.all([
      api.list_folders().then((rows) => rows.filter((f) => !isArchiveFolderId(f.id))),
      api.list_all_conversations(),
    ]);

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
      // Recover legacy spawned chats saved under the nonexistent literal
      // "default" folder (and any other orphaned folder id) into the root.
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

    setArchiveChats(mapConversations(archive));

    // Hide linked group hubs from the tree (folder click opens them), but keep
    // them in hubChats so EditorGroupPane can resolve isGroup / members.
    const hubIds = new Set(
      folderRows.map((f) => String(f.group_hub_id || "").trim()).filter(Boolean),
    );
    const hubs: FolderItem["chats"] = [];
    for (const rows of chatsByFolder.values()) {
      for (const c of rows) {
        if (c.isGroup && hubIds.has(c.id)) hubs.push(c);
      }
    }
    setHubChats(hubs);

    const rootRaw = chatsByFolder.get("") ?? [];
    setRootChats(rootRaw.filter((c) => !(c.isGroup && hubIds.has(c.id))));

    setFolders((prev) => {
      const expandedById = new Map<string, boolean>();
      const collectExpanded = (items: FolderItem[]) => {
        for (const item of items) {
          expandedById.set(item.id, item.expanded);
          collectExpanded(item.children);
        }
      };
      collectExpanded(prev);
      return buildFolderTree(folderRows, chatsByFolder, expandedById);
    });
    setFoldersLoaded(true);
  }, []);

  useEffect(() => {
    setFoldersLoaded(false);
    void load();
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

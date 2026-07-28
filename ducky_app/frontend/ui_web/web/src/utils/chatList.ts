import type { ChatTab } from "../types/panel";

export interface ListedFolder {
  id: string;
  name: string;
  parentId: string;
  sortOrder: number;
}

export interface ListedConversation {
  id: string;
  title: string;
  folderId: string;
  sortOrder: number;
  messageCount: number;
}

export interface ChatListPayload {
  folders: ListedFolder[];
  conversations: ListedConversation[];
}

export interface ListedChatRef {
  id: string;
  title: string;
  folderId?: string;
  messageCount?: number;
}

const LIST_CHATS_TOOL = "ducky_list_chats";
const SINGLE_CHAT_TOOLS = new Set([
  "ducky_read_chat",
  "ducky_create_chat",
  "ducky_rename_chat",
  "ducky_move_chat",
]);

function parseToolResultPayload(raw: unknown): Record<string, unknown> | null {
  if (raw === undefined || raw === null || raw === "") return null;
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    if (typeof parsed === "object" && parsed !== null && "data" in (parsed as object)) {
      const inner = (parsed as { data?: unknown }).data;
      if (typeof inner === "string") return parseToolResultPayload(inner);
      if (typeof inner === "object" && inner !== null) return inner as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

function readFolder(raw: Record<string, unknown>): ListedFolder {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? "Folder").trim() || "Folder",
    parentId: String(raw.parent_id ?? raw.parentId ?? ""),
    sortOrder: Number(raw.sort_order ?? raw.sortOrder ?? 0),
  };
}

function readConversation(raw: Record<string, unknown>): ListedConversation | null {
  const id = String(raw.id ?? "").trim();
  if (!id) return null;
  return {
    id,
    title: String(raw.title ?? "Ducky").trim() || "Ducky",
    folderId: String(raw.folder_id ?? raw.folderId ?? "default"),
    sortOrder: Number(raw.sort_order ?? raw.sortOrder ?? 0),
    messageCount: Number(raw.message_count ?? raw.messageCount ?? 0),
  };
}

export function parseChatListFromToolResult(toolName: string, resultRaw: unknown): ChatListPayload | null {
  if (toolName !== LIST_CHATS_TOOL) return null;
  const payload = parseToolResultPayload(resultRaw);
  if (!payload) return null;
  const foldersRaw = payload.folders;
  const convsRaw = payload.conversations;
  if (!Array.isArray(foldersRaw) || !Array.isArray(convsRaw)) return null;
  const folders = foldersRaw
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .map(readFolder)
    .filter((f) => f.id);
  const conversations = convsRaw
    .filter((c): c is Record<string, unknown> => typeof c === "object" && c !== null)
    .map(readConversation)
    .filter((c): c is ListedConversation => c !== null);
  return { folders, conversations };
}

export function parseListedChatFromToolResult(toolName: string, resultRaw: unknown): ListedChatRef | null {
  if (!SINGLE_CHAT_TOOLS.has(toolName)) return null;
  const payload = parseToolResultPayload(resultRaw);
  if (!payload) return null;
  const id = String(payload.id ?? payload.conv_id ?? "").trim();
  if (!id) return null;
  const messages = payload.messages;
  const messageCount = Array.isArray(messages) ? messages.length : undefined;
  return {
    id,
    title: String(payload.title ?? "Ducky").trim() || "Ducky",
    folderId: String(payload.folder_id ?? payload.folderId ?? "").trim() || undefined,
    messageCount,
  };
}

export function resolveChatTab(id: string, title: string, allChats: ChatTab[]): ChatTab {
  return allChats.find((c) => c.id === id) ?? { id, name: title };
}

export function folderNameById(folders: ListedFolder[], folderId: string): string {
  const found = folders.find((f) => f.id === folderId);
  return found?.name ?? folderId;
}

export interface ChatListSection {
  folder: ListedFolder;
  depth: number;
  conversations: ListedConversation[];
}

/** Flatten folder tree with chats grouped under each folder (depth-first). */
export function buildChatListSections(payload: ChatListPayload): ChatListSection[] {
  const { folders, conversations } = payload;
  const byParent = new Map<string, ListedFolder[]>();
  for (const folder of folders) {
    const key = folder.parentId || "";
    const list = byParent.get(key) ?? [];
    list.push(folder);
    byParent.set(key, list);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.sortOrder - b.sortOrder || a.name.localeCompare(b.name));
  }

  const sections: ChatListSection[] = [];
  const walk = (parentId: string, depth: number) => {
    const children = byParent.get(parentId) ?? [];
    for (const folder of children) {
      const convs = conversations
        .filter((c) => c.folderId === folder.id)
        .sort((a, b) => a.sortOrder - b.sortOrder || a.title.localeCompare(b.title));
      sections.push({ folder, depth, conversations: convs });
      walk(folder.id, depth + 1);
    }
  };
  walk("", 0);

  const listedFolderIds = new Set(folders.map((f) => f.id));
  const orphanConvs = conversations
    .filter((c) => !listedFolderIds.has(c.folderId))
    .sort((a, b) => a.sortOrder - b.sortOrder || a.title.localeCompare(b.title));
  if (orphanConvs.length > 0) {
    sections.push({
      folder: { id: "_other", name: "Other", parentId: "", sortOrder: 9999 },
      depth: 0,
      conversations: orphanConvs,
    });
  }

  return sections;
}

/**
 * Duck-Tac-Toe branded chat + board binding.
 * Primary surface: chat tab with collapsible board aside (not a separate plugin tab).
 */
import { DEFAULT_BUNDLED_DUCKY_STYLE } from "../generated/bundledDuckies";
import type { ChatTab, DuckyConfigDto } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";

export const DUCKTACTOE_PLUGIN_ID = "ducktactoe";
export const DUCKTACTOE_BOARD_PANEL_ID = "board";

export const DUCKTACTOE_CHAT_NAME = "Duck-Tac-Toe";

export const DUCKTACTOE_PERSONALITY =
  "You are a playful Duck-Tac-Toe opponent in this branded game chat. " +
  "Game talk (won/win/draw/move/board/rematch) always means Duck-Tac-Toe — first tool ducktactoe_state. " +
  "Never grep Verse or invent a UEFN/roguelike victory for those messages. " +
  "Play via ducktactoe_* tools and narrate briefly. " +
  "Only do UEFN/project work when the human clearly asks for it. " +
  "Never claim a win or draw without a successful ducktactoe_move / ducktactoe_state result.";

/** Fixed brand config for the game chat (no profile system). */
export function ducktactoeChatConfig(): DuckyConfigDto {
  return {
    title: DUCKTACTOE_CHAT_NAME,
    ducky_name: DUCKTACTOE_CHAT_NAME,
    ducky_style: DEFAULT_BUNDLED_DUCKY_STYLE,
    ducky_personality: DUCKTACTOE_PERSONALITY,
    // Keep Ducky (not Cursor/Codex) so the game does not inherit a coding-agent default.
    coding_agent: "ducky",
    disabled_tool_ids: [],
  };
}

export function isDucktactoeBoardTab(pluginId: string, panelId: string): boolean {
  return (
    pluginId.trim().toLowerCase() === DUCKTACTOE_PLUGIN_ID &&
    panelId.trim().toLowerCase() === DUCKTACTOE_BOARD_PANEL_ID
  );
}

export function isDucktactoeChat(chat: {
  id?: string;
  name?: string;
  duckyName?: string;
  duckyPersonality?: string;
}): boolean {
  if (gameChat && chat.id && chat.id === gameChat.id) return true;
  const name = (chat.duckyName || chat.name || "").trim();
  if (name === DUCKTACTOE_CHAT_NAME) return true;
  const pers = (chat.duckyPersonality || "").trim();
  return Boolean(pers && pers === DUCKTACTOE_PERSONALITY);
}

/** Singleton game chat (header + chat tabs share one conversation). */
let gameChat: ChatTab | null = null;
let pendingGameChat: Promise<ChatTab | null> | null = null;

/** Legacy: board plugin-tab session binds (redirect path). */
const boundByTabId = new Map<string, ChatTab>();
const pendingByTabId = new Map<string, Promise<ChatTab | null>>();
let suppressRemoteOpenDepth = 0;

export function getBoundDucktactoeChat(tabId: string): ChatTab | undefined {
  return boundByTabId.get(tabId) ?? gameChat ?? undefined;
}

export function setBoundDucktactoeChat(tabId: string, chat: ChatTab): void {
  boundByTabId.set(tabId, chat);
  gameChat = chat;
}

export function clearBoundDucktactoeChat(tabId: string): void {
  boundByTabId.delete(tabId);
  pendingByTabId.delete(tabId);
}

/** True when chats_changed should not steal focus into a full chat tab. */
export function shouldSuppressRemoteChatOpen(conv: { id?: string; title?: string }): boolean {
  if (suppressRemoteOpenDepth > 0) {
    const title = (conv.title || "").trim();
    if (!title || title === DUCKTACTOE_CHAT_NAME) return true;
  }
  const id = (conv.id || "").trim();
  if (!id) return false;
  if (gameChat?.id === id) return true;
  for (const chat of boundByTabId.values()) {
    if (chat.id === id) return true;
  }
  return false;
}

async function createBrandedChat(): Promise<ChatTab | null> {
  const api = getApi();
  if (!api?.create_conversation) return null;
  const config = ducktactoeChatConfig();
  suppressRemoteOpenDepth += 1;
  try {
    const conv = await api.create_conversation("", DEFAULT_BUNDLED_DUCKY_STYLE, undefined, config);
    return {
      id: conv.id,
      name: conv.title || DUCKTACTOE_CHAT_NAME,
      duckyStyle: conv.ducky_style || DEFAULT_BUNDLED_DUCKY_STYLE,
      duckyName: conv.ducky_name || DUCKTACTOE_CHAT_NAME,
      duckyPersonality: conv.ducky_personality || DUCKTACTOE_PERSONALITY,
      model: conv.model,
      provider: conv.provider,
      codingAgent: conv.coding_agent || "ducky",
    };
  } finally {
    suppressRemoteOpenDepth = Math.max(0, suppressRemoteOpenDepth - 1);
  }
}

async function ensureDuckyAgent(chat: ChatTab): Promise<ChatTab> {
  if ((chat.codingAgent || "ducky") === "ducky") return chat;
  const api = getApi();
  if (!api?.set_conversation_coding_agent) return chat;
  try {
    const res = await api.set_conversation_coding_agent(chat.id, "ducky");
    if (res?.ok) {
      return {
        ...chat,
        codingAgent: res.coding_agent || "ducky",
        model: res.model ?? chat.model,
        provider: res.provider ?? chat.provider,
      };
    }
  } catch {
    /* keep chat usable even if agent switch fails */
  }
  return chat;
}

/** Create or reuse the singleton Duck-Tac-Toe conversation. */
export async function ensureDucktactoeGameChat(
  existingChats?: readonly ChatTab[],
): Promise<ChatTab | null> {
  if (gameChat) return gameChat;
  const found = (existingChats || []).find((c) => isDucktactoeChat(c));
  if (found) {
    gameChat = await ensureDuckyAgent(found);
    return gameChat;
  }
  if (pendingGameChat) return pendingGameChat;

  pendingGameChat = (async () => {
    const chat = await createBrandedChat();
    if (chat) gameChat = await ensureDuckyAgent(chat);
    return gameChat;
  })().finally(() => {
    pendingGameChat = null;
  });
  return pendingGameChat;
}

/** @deprecated Prefer ensureDucktactoeGameChat — kept for plugin-tab redirect. */
export async function ensureDucktactoeBoardChat(tabId: string): Promise<ChatTab | null> {
  const existing = boundByTabId.get(tabId);
  if (existing) return existing;

  const pending = pendingByTabId.get(tabId);
  if (pending) return pending;

  const work = (async (): Promise<ChatTab | null> => {
    const chat = await ensureDucktactoeGameChat();
    if (chat) boundByTabId.set(tabId, chat);
    return chat;
  })().finally(() => {
    pendingByTabId.delete(tabId);
  });

  pendingByTabId.set(tabId, work);
  return work;
}

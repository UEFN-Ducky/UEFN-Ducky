import type { ChatMessage } from "../types/panel";

/**
 * In-memory snapshot of a chat's live view, so switching tabs (or a hidden pane
 * becoming visible) restores instantly without a flash. Cleared on app restart —
 * the backend conversation file is always the source of truth.
 */
export interface CachedChatMessagesState {
  messages: ChatMessage[];
  streamBuffer: string;
  streamThinking: string;
  optimisticRunning: boolean;
  hasNewBelow: boolean;
  isAtBottom: boolean;
  activeRunId: string | null;
  stoppedRun: boolean;
}

const MAX_CACHED_CHATS = 24;
const cache = new Map<string, CachedChatMessagesState>();

function touch(chatId: string, state: CachedChatMessagesState): void {
  cache.delete(chatId);
  cache.set(chatId, state);
  while (cache.size > MAX_CACHED_CHATS) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

export function getCachedChatMessages(chatId: string): CachedChatMessagesState | undefined {
  const hit = cache.get(chatId);
  if (!hit) return undefined;
  // Refresh LRU order on read.
  touch(chatId, hit);
  return hit;
}

export function setCachedChatMessages(chatId: string, state: CachedChatMessagesState): void {
  touch(chatId, state);
}

export function clearCachedChatMessages(chatId: string): void {
  cache.delete(chatId);
}

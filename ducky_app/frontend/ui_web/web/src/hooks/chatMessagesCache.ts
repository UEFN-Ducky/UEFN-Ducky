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
const MAX_IDLE_BYTES = 16 * 1024 * 1024;
const cache = new Map<string, { state: CachedChatMessagesState; bytes: number }>();
// Reducer messages and arrays are immutable. Weak keys avoid making the
// accounting itself retain evicted conversations, or rescanning on each delta.
const messageSizes = new WeakMap<ChatMessage, number>();
const arraySizes = new WeakMap<ChatMessage[], number>();
let idleBytes = 0;

/** Conservative payload estimate, not an exact measurement of the JS heap. */
function estimateBytes(value: unknown, seen = new WeakSet<object>(), depth = 0): number {
  if (typeof value === "string") return value.length * 2;
  if (value === null || typeof value !== "object") return 8;
  if (depth > 32) return MAX_IDLE_BYTES + 1;
  if (seen.has(value)) return 0;
  seen.add(value);
  let bytes = 64;
  for (const key in value) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
    bytes += key.length * 2 + estimateBytes((value as Record<string, unknown>)[key], seen, depth + 1);
    if (bytes > MAX_IDLE_BYTES) break;
  }
  return bytes;
}

function snapshotBytes(state: CachedChatMessagesState): number {
  let bytes = arraySizes.get(state.messages);
  if (bytes === undefined) {
    bytes = 64;
    for (const message of state.messages) {
      let size = messageSizes.get(message);
      if (size === undefined) {
        size = estimateBytes(message);
        messageSizes.set(message, size);
      }
      bytes += size + 8;
      if (bytes > MAX_IDLE_BYTES) break;
    }
    arraySizes.set(state.messages, bytes);
  }
  return bytes + 256 + (state.streamBuffer.length + state.streamThinking.length) * 2;
}

export function getCachedChatMessages(chatId: string): CachedChatMessagesState | undefined {
  const hit = cache.get(chatId);
  if (!hit) return undefined;
  // Refresh LRU order on read.
  cache.delete(chatId);
  cache.set(chatId, hit);
  return hit.state;
}

export function setCachedChatMessages(chatId: string, state: CachedChatMessagesState): void {
  clearCachedChatMessages(chatId);
  const bytes = snapshotBytes(state);
  if (!state.optimisticRunning && bytes > MAX_IDLE_BYTES) return;
  cache.set(chatId, { state, bytes });
  if (!state.optimisticRunning) idleBytes += bytes;
  while (cache.size > MAX_CACHED_CHATS) {
    clearCachedChatMessages(cache.keys().next().value!);
  }
  // A live stream may not yet be persisted; preserve its existing snapshot
  // behavior. Completed histories can always be loaded again from the backend.
  for (const [id, entry] of cache) {
    if (idleBytes <= MAX_IDLE_BYTES) break;
    if (!entry.state.optimisticRunning) clearCachedChatMessages(id);
  }
}

export function clearCachedChatMessages(chatId: string): void {
  const entry = cache.get(chatId);
  if (entry && !entry.state.optimisticRunning) idleBytes -= entry.bytes;
  cache.delete(chatId);
}

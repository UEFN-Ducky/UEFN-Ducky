import type { AgentMode } from "../types/panel";
import { boundedGet, boundedSet } from "../utils/boundedMap";

export interface CachedChatComposerState {
  inputText: string;
  agentMode: AgentMode;
  selectedModel: string;
  selectedModelDisplayName: string;
  codingAgent?: string;
}

/** Same cap as the message cache: a session can open any number of chats. */
const MAX_CACHED = 24;
const cache = new Map<string, CachedChatComposerState>();
const pendingDraftByChatId = new Map<string, string>();
const draftListeners = new Map<string, Set<() => void>>();

export function getCachedChatComposer(chatId: string): CachedChatComposerState | undefined {
  return boundedGet(cache, chatId);
}

export function setCachedChatComposer(chatId: string, state: CachedChatComposerState): void {
  boundedSet(cache, chatId, state, MAX_CACHED);
}

export function enqueueComposerDraft(chatId: string, text: string): void {
  // A draft for a chat that is never reopened would otherwise sit here forever.
  boundedSet(pendingDraftByChatId, chatId, text, MAX_CACHED);
  for (const listener of draftListeners.get(chatId) ?? []) listener();
}

export function takeComposerDraft(chatId: string): string | undefined {
  const draft = pendingDraftByChatId.get(chatId);
  if (draft === undefined) return undefined;
  pendingDraftByChatId.delete(chatId);
  return draft;
}

export function subscribeComposerDraft(chatId: string, listener: () => void): () => void {
  let set = draftListeners.get(chatId);
  if (!set) {
    set = new Set();
    draftListeners.set(chatId, set);
  }
  set.add(listener);
  return () => {
    set!.delete(listener);
    if (set!.size === 0) draftListeners.delete(chatId);
  };
}

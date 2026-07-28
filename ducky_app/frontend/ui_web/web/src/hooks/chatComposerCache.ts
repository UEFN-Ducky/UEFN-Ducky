import type { AgentMode } from "../types/panel";

export interface CachedChatComposerState {
  inputText: string;
  agentMode: AgentMode;
  selectedModel: string;
  selectedModelDisplayName: string;
  codingAgent?: string;
}

const cache = new Map<string, CachedChatComposerState>();
const pendingDraftByChatId = new Map<string, string>();
const draftListeners = new Map<string, Set<() => void>>();

export function getCachedChatComposer(chatId: string): CachedChatComposerState | undefined {
  return cache.get(chatId);
}

export function setCachedChatComposer(chatId: string, state: CachedChatComposerState): void {
  cache.set(chatId, state);
}

export function enqueueComposerDraft(chatId: string, text: string): void {
  pendingDraftByChatId.set(chatId, text);
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

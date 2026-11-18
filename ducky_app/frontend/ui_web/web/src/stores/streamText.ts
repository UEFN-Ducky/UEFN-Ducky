import { useCallback, useSyncExternalStore } from "react";

/** The live stream text per chat, so readers (voice speak-along) update without the composer re-rendering. */
const texts = new Map<string, string>();
const listeners = new Map<string, Set<() => void>>();

export function getStreamText(chatId: string): string {
  return texts.get(chatId) ?? "";
}

export function setStreamText(chatId: string, text: string): void {
  if (getStreamText(chatId) === text) return;
  if (text) texts.set(chatId, text);
  else texts.delete(chatId);
  listeners.get(chatId)?.forEach((fn) => fn());
}

export function subscribeStreamText(chatId: string, fn: () => void): () => void {
  let set = listeners.get(chatId);
  if (!set) listeners.set(chatId, (set = new Set()));
  set.add(fn);
  return () => {
    set.delete(fn);
    if (!set.size) listeners.delete(chatId);
  };
}

export function useStreamText(chatId: string): string {
  const subscribe = useCallback((fn: () => void) => subscribeStreamText(chatId, fn), [chatId]);
  return useSyncExternalStore(subscribe, () => getStreamText(chatId));
}

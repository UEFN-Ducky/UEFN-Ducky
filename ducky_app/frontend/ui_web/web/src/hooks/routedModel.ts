/**
 * The model a router gateway (Ducky Auto) last ran a chat's step on, from the status event's
 * `routed_model` ("gateway:model"). Only routers send it, so plain chats never show it.
 * ponytail: kept in memory; a chat reopened after a restart shows it again from its next step.
 */
import { useSyncExternalStore } from "react";

const routed = new Map<string, string>();
const listeners = new Set<() => void>();

export function setRoutedModel(chatId: string, model: string): void {
  const id = (chatId || "").trim();
  const value = (model || "").trim();
  if (!id || !value || routed.get(id) === value) return;
  routed.set(id, value);
  for (const fn of listeners) fn();
}

export function getRoutedModel(chatId: string): string {
  return routed.get((chatId || "").trim()) || "";
}

/** "gemini:gemini-3.8-flash" → "gemini-3.8-flash". */
export function routedModelLabel(qualified: string): string {
  const i = qualified.indexOf(":");
  return i >= 0 ? qualified.slice(i + 1) : qualified;
}

export function useRoutedModel(chatId: string): string {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    () => getRoutedModel(chatId),
  );
}

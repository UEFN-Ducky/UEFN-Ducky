import type { AgentMode, MessageAttachmentDto } from "../types/panel";

export interface QueuedPrompt {
  id: string;
  text: string;
  attachments: MessageAttachmentDto[];
  mode: AgentMode;
  model: string;
}

/** Pure helpers — easy to unit-test without the store. */

export function appendPrompt(items: QueuedPrompt[], item: QueuedPrompt): QueuedPrompt[] {
  return [...items, item];
}

export function removePrompt(items: QueuedPrompt[], id: string): QueuedPrompt[] {
  return items.filter((p) => p.id !== id);
}

export function updatePromptText(items: QueuedPrompt[], id: string, text: string): QueuedPrompt[] {
  const trimmed = text.trim();
  if (!trimmed) {
    const item = items.find((p) => p.id === id);
    // Image/file-only prompts stay queued with empty text; bare text prompts are removed.
    if (!item?.attachments.length) return removePrompt(items, id);
  }
  return items.map((p) => (p.id === id ? { ...p, text: trimmed } : p));
}

/** Move item to index 0 (next to run). No-op if missing or already first. */
export function movePromptToFront(items: QueuedPrompt[], id: string): QueuedPrompt[] {
  const i = items.findIndex((p) => p.id === id);
  if (i <= 0) return items;
  const next = items.slice();
  const [item] = next.splice(i, 1);
  next.unshift(item);
  return next;
}

export function shiftPrompt(items: QueuedPrompt[]): {
  next: QueuedPrompt | null;
  rest: QueuedPrompt[];
} {
  if (items.length === 0) return { next: null, rest: items };
  const [next, ...rest] = items;
  return { next, rest };
}

export function makeQueuedPrompt(
  text: string,
  opts: {
    attachments?: MessageAttachmentDto[];
    mode: AgentMode;
    model: string;
    id?: string;
  },
): QueuedPrompt | null {
  const trimmed = text.trim();
  const attachments = opts.attachments ?? [];
  if (!trimmed && attachments.length === 0) return null;
  return {
    id: opts.id ?? `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    text: trimmed,
    attachments,
    mode: opts.mode,
    model: opts.model,
  };
}

/* ── per-chat store (session memory, survives pane remount) ── */

const queues = new Map<string, QueuedPrompt[]>();
const listeners = new Map<string, Set<() => void>>();

function notify(chatId: string): void {
  for (const fn of listeners.get(chatId) ?? []) fn();
}

export function getPromptQueue(chatId: string): QueuedPrompt[] {
  return queues.get(chatId) ?? [];
}

export function setPromptQueue(chatId: string, items: QueuedPrompt[]): void {
  if (items.length === 0) queues.delete(chatId);
  else queues.set(chatId, items);
  notify(chatId);
}

export function enqueuePrompt(chatId: string, item: QueuedPrompt): void {
  setPromptQueue(chatId, appendPrompt(getPromptQueue(chatId), item));
}

export function takeNextPrompt(chatId: string): QueuedPrompt | null {
  const { next, rest } = shiftPrompt(getPromptQueue(chatId));
  if (!next) return null;
  setPromptQueue(chatId, rest);
  return next;
}

/** Prevents Strict Mode / overlapping idle effects from draining two items at once. */
const drainLocks = new Set<string>();

export function takeNextPromptForDrain(chatId: string): QueuedPrompt | null {
  if (drainLocks.has(chatId)) return null;
  const next = takeNextPrompt(chatId);
  if (next) drainLocks.add(chatId);
  return next;
}

export function releasePromptDrainLock(chatId: string): void {
  drainLocks.delete(chatId);
}

export function isPromptDrainLocked(chatId: string): boolean {
  return drainLocks.has(chatId);
}

export function subscribePromptQueue(chatId: string, listener: () => void): () => void {
  let set = listeners.get(chatId);
  if (!set) {
    set = new Set();
    listeners.set(chatId, set);
  }
  set.add(listener);
  return () => {
    set!.delete(listener);
    if (set!.size === 0) listeners.delete(chatId);
  };
}

/** Test helper — wipe all queues. */
export function _resetPromptQueuesForTests(): void {
  queues.clear();
  listeners.clear();
  drainLocks.clear();
}

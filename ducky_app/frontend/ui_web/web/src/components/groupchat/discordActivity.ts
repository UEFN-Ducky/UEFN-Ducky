/** Per-bot unread/activity counts for header badges (commands + new messages). */

import { useSyncExternalStore } from "react";

const counts = new Map<string, number>();
const listeners = new Set<() => void>();
let version = 0;
/** Cached Map for useSyncExternalStore — MUST be referentially stable until counts change. */
let cachedMap: ReadonlyMap<string, number> = new Map();
let cachedMapVersion = -1;
/** Bot currently open in a visible Discord panel — don't badge that one. */
let viewingBotId: string | null = null;

function emit() {
  version += 1;
  for (const fn of [...listeners]) fn();
}

function getActivityMapSnapshot(): ReadonlyMap<string, number> {
  if (cachedMapVersion !== version) {
    cachedMapVersion = version;
    cachedMap = new Map(counts);
  }
  return cachedMap;
}

export function setDiscordViewingBot(botId: string | null): void {
  viewingBotId = botId ? (botId.trim() || "default") : null;
  if (viewingBotId) clearDiscordActivity(viewingBotId);
}

export function getDiscordViewingBot(): string | null {
  return viewingBotId;
}

export function bumpDiscordActivity(botId: string, by = 1): void {
  const bid = (botId || "default").trim() || "default";
  if (bid === viewingBotId) return;
  counts.set(bid, (counts.get(bid) || 0) + Math.max(1, by));
  emit();
}

export function clearDiscordActivity(botId: string): void {
  const bid = (botId || "default").trim() || "default";
  if (!counts.has(bid)) return;
  counts.delete(bid);
  emit();
}

export function getDiscordActivity(botId: string): number {
  return counts.get((botId || "default").trim() || "default") || 0;
}

export function getDiscordActivityTotal(): number {
  let n = 0;
  for (const v of counts.values()) n += v;
  return n;
}

export function formatDiscordBadge(count: number): string | null {
  if (count <= 0) return null;
  return count > 9 ? "9+" : String(count);
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useDiscordActivityTotal(): number {
  return useSyncExternalStore(subscribe, getDiscordActivityTotal);
}

export function useDiscordActivity(botId: string): number {
  return useSyncExternalStore(subscribe, () => getDiscordActivity(botId));
}

/** Snapshot for dropdown rows (re-renders when any count changes). */
export function useDiscordActivityMap(): ReadonlyMap<string, number> {
  return useSyncExternalStore(subscribe, getActivityMapSnapshot);
}

/** Test/helper — same snapshot getActivityMapSnapshot uses (stable until emit). */
export function _getDiscordActivityMapSnapshotForTests(): ReadonlyMap<string, number> {
  return getActivityMapSnapshot();
}

/** Test helper — reset module state. */
export function _resetDiscordActivityForTests(): void {
  counts.clear();
  listeners.clear();
  version = 0;
  cachedMap = new Map();
  cachedMapVersion = -1;
  viewingBotId = null;
}

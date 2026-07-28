/**
 * Per-chat turn timer — how long the agent has been (or was) working.
 * Shared by the chat footer, hover card, and any other UI that needs the same clock.
 */

export type ChatTurnTimer = {
  startedAt: number;
  /** null while the turn is still running. */
  endedAt: number | null;
};

const timers = new Map<string, ChatTurnTimer>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeChatTurnTimers(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Snapshot token for useSyncExternalStore — bumps on every timer change. */
export function getChatTurnTimersVersion(): number {
  return version;
}

export function getChatTurnTimer(chatId: string): ChatTurnTimer | null {
  if (!chatId) return null;
  return timers.get(chatId) ?? null;
}

/** Start (or keep) a live turn clock for this chat. */
export function markChatTurnRunning(chatId: string, at = Date.now()): void {
  if (!chatId) return;
  const cur = timers.get(chatId);
  if (cur && cur.endedAt == null) return;
  timers.set(chatId, { startedAt: at, endedAt: null });
  emit();
}

/** Freeze the clock when the turn ends — keeps the final duration for the chat. */
export function markChatTurnIdle(chatId: string, at = Date.now()): void {
  if (!chatId) return;
  const cur = timers.get(chatId);
  if (!cur || cur.endedAt != null) return;
  timers.set(chatId, { startedAt: cur.startedAt, endedAt: at });
  emit();
}

/** Diff two running-id sets and latch start/stop for each chat. */
export function syncChatTurnTimersFromRunningSet(
  prev: ReadonlySet<string>,
  next: ReadonlySet<string>,
  at = Date.now(),
): void {
  let changed = false;
  for (const id of next) {
    if (prev.has(id)) continue;
    const cur = timers.get(id);
    if (cur && cur.endedAt == null) continue;
    timers.set(id, { startedAt: at, endedAt: null });
    changed = true;
  }
  for (const id of prev) {
    if (next.has(id)) continue;
    const cur = timers.get(id);
    if (!cur || cur.endedAt != null) continue;
    timers.set(id, { startedAt: cur.startedAt, endedAt: at });
    changed = true;
  }
  if (changed) emit();
}

/** Elapsed ms at `now` (live) or at endedAt (finished). */
export function chatTurnElapsedMs(timer: ChatTurnTimer | null, now = Date.now()): number | null {
  if (!timer) return null;
  const end = timer.endedAt ?? now;
  return Math.max(0, end - timer.startedAt);
}

export function formatElapsedMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = ms / 1000;
  if (totalSec < 60) {
    return totalSec < 10 ? `${totalSec.toFixed(1)}s` : `${Math.round(totalSec)}s`;
  }
  const mins = Math.floor(totalSec / 60);
  const secs = Math.floor(totalSec % 60);
  if (mins < 60) return `${mins}m ${secs.toString().padStart(2, "0")}s`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m`;
}

/** Jump Changes to a later writer's row after the blocker modal opens their chat. */

let pending: { runId: string; seq: number } | null = null;
const listeners = new Set<() => void>();

export function requestChangesFocus(runId: string, seq: number): void {
  pending = { runId, seq };
  for (const fn of listeners) fn();
}

export function takeChangesFocus(): { runId: string; seq: number } | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribeChangesFocus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

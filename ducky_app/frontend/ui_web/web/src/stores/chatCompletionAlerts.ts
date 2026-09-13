// Copy-on-write: useSyncExternalStore compares snapshots with Object.is, so mutating
// one Set in place never re-rendered subscribers (a dismissed alert's badge lingered
// until something unrelated re-rendered the tab strip).
let alertChatIds: ReadonlySet<string> = new Set<string>();
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) {
    listener();
  }
}

export function subscribeCompletionAlerts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getCompletionAlertChatIds(): ReadonlySet<string> {
  return alertChatIds;
}

export function setCompletionAlert(chatId: string) {
  const id = chatId.trim();
  if (!id || alertChatIds.has(id)) return;
  alertChatIds = new Set([...alertChatIds, id]);
  emit();
}

export function dismissCompletionAlert(chatId: string) {
  const id = chatId.trim();
  if (!id || !alertChatIds.has(id)) return;
  const next = new Set(alertChatIds);
  next.delete(id);
  alertChatIds = next;
  emit();
}

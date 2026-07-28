const alertChatIds = new Set<string>();
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
  alertChatIds.add(id);
  emit();
}

export function dismissCompletionAlert(chatId: string) {
  const id = chatId.trim();
  if (!id || !alertChatIds.has(id)) return;
  alertChatIds.delete(id);
  emit();
}

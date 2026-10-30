/**
 * Which chat pane is focused / visible — used to bind orphan ask_user calls
 * (empty conv_id) into the chat instead of the header activity list, and told to
 * the phone page so a reopened Remote View lands on the same chat.
 */
let focusedChatId = "";
const listeners = new Set<(chatId: string) => void>();

export function setFocusedChatForAsk(chatId: string): void {
  const next = String(chatId || "").trim();
  if (next === focusedChatId) return;
  focusedChatId = next;
  for (const fn of listeners) fn(next);
}

export function getFocusedChatForAsk(): string {
  return focusedChatId;
}

/** Called with the new id whenever the focused chat changes. Returns an unsubscribe. */
export function onFocusedChatChange(fn: (chatId: string) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Test helper. */
export function _resetFocusedChatForAsk(): void {
  focusedChatId = "";
  listeners.clear();
}

/** "Take me to the card": the chat that opens next scrolls to its unanswered question or command. */
const pending = new Set<string>();
const listeners = new Set<(id: string) => void>();

export function requestPendingCard(convId: string): void {
  const id = (convId || "").trim();
  if (!id) return;
  pending.add(id);
  for (const listener of listeners) listener(id);
}

/** The chat's message list calls `scroll` once per request (now, or as soon as it mounts). */
export function subscribePendingCard(convId: string, scroll: () => void): () => void {
  const consume = (target: string) => {
    if (target === convId && pending.delete(convId)) scroll();
  };
  listeners.add(consume);
  consume(convId);
  return () => {
    listeners.delete(consume);
  };
}

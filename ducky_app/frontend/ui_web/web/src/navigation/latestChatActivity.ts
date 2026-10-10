const pending = new Set<string>();
const listeners = new Set<(id: string) => void>();
export function requestLatestChatActivity(id: string): void {
  pending.add(id);
  for (const listener of listeners) listener(id);
}
export function subscribeLatestChatActivity(id: string, scroll: () => void): () => void {
  const consume = (target: string) => {
    if (target === id && pending.delete(id)) scroll();
  };
  listeners.add(consume);
  consume(id);
  return () => { listeners.delete(consume); };
}

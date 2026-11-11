/**
 * The chats this window shows right now, wherever their pane sits: an editor tab, a plugin's
 * chat dock, a pop-up card. Each visible ChatPane marks its chat here.
 *
 * The app streams a turn's live text only to chats on screen and sends the rest one status
 * line a second ("Working · 158s"). That list used to come from the editor tabs alone, so a
 * chat docked in a plugin panel got the status line while the person was reading it, and its
 * reply showed all at once when the turn ended.
 */
const counts = new Map<string, number>();
const listeners = new Set<() => void>();

function changed(): void {
  for (const fn of [...listeners]) fn();
}

/** A visible pane shows this chat; call the returned function when it hides or unmounts. */
export function markChatShown(chatId: string): () => void {
  if (!chatId) return () => {};
  const before = counts.get(chatId) ?? 0;
  counts.set(chatId, before + 1);
  if (before === 0) changed();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const left = (counts.get(chatId) ?? 1) - 1;
    if (left > 0) {
      counts.set(chatId, left);
      return;
    }
    counts.delete(chatId);
    changed();
  };
}

/** Every chat a visible pane shows, sorted. */
export function shownChatIds(): string[] {
  return [...counts.keys()].sort();
}

/** Called when a chat starts or stops being shown (not when a second pane shows the same one). */
export function onShownChatsChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function resetShownChatsForTests(): void {
  counts.clear();
  listeners.clear();
}

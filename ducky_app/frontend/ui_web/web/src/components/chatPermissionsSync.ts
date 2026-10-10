const CHANGED_EVENT = "ducky:chat-permissions";

type Detail = { convId?: string; source?: object };

/** Tell every other view of this chat's approvals (the composer button, the context panel)
 *  to re-read them. `source` is the view that made the change: it already holds the result. */
export function announceChatPermissions(convId: string, source?: object): void {
  window.dispatchEvent(new CustomEvent<Detail>(CHANGED_EVENT, { detail: { convId, source } }));
}

export function subscribeChatPermissions(convId: string, listener: () => void, self?: object): () => void {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<Detail>).detail;
    if (detail?.convId === convId && (!self || detail.source !== self)) listener();
  };
  window.addEventListener(CHANGED_EVENT, handler);
  return () => window.removeEventListener(CHANGED_EVENT, handler);
}

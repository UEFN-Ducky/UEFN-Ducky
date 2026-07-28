/** Open a ducky chat tab so per-chat Translate can run on its messages. */

export type OpenTranslatedChatTarget = {
  id: string;
  name: string;
  duckyStyle?: string;
  isGroup?: boolean;
};

let openHandler: ((chat: OpenTranslatedChatTarget) => void) | null = null;

export function registerOpenTranslatedChat(
  fn: (chat: OpenTranslatedChatTarget) => void,
): () => void {
  openHandler = fn;
  return () => {
    if (openHandler === fn) openHandler = null;
  };
}

export function openTranslatedChat(chat: OpenTranslatedChatTarget): void {
  const id = String(chat?.id || "").trim();
  if (!id || !openHandler) return;
  openHandler({
    id,
    name: String(chat.name || "").trim() || "Ducky",
    duckyStyle: chat.duckyStyle,
    isGroup: Boolean(chat.isGroup),
  });
}

/** Ask the Translation boot script to walk a live chat message list. */
export function requestChatTranslateWalk(): void {
  try {
    window.dispatchEvent(
      new CustomEvent("uefn-translate-scope", {
        detail: { selector: ".virtual-chat-message-list-root:not([data-no-translate])" },
      }),
    );
  } catch {
    /* ignore */
  }
}

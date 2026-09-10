/** Open/focus a non-group chat so the composer tour can spotlight controls. */

let showChatComposer: (() => void) | null = null;

export function registerShowChatComposer(fn: () => void): () => void {
  showChatComposer = fn;
  return () => {
    if (showChatComposer === fn) showChatComposer = null;
  };
}

export function requestShowChatComposer(): void {
  showChatComposer?.();
}

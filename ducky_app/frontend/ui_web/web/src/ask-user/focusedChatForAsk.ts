/**
 * Which chat pane is focused / visible — used to bind orphan ask_user calls
 * (empty conv_id) into the chat dock instead of the modal.
 */
let focusedChatId = "";

export function setFocusedChatForAsk(chatId: string): void {
  focusedChatId = String(chatId || "").trim();
}

export function getFocusedChatForAsk(): string {
  return focusedChatId;
}

/** Test helper. */
export function _resetFocusedChatForAsk(): void {
  focusedChatId = "";
}

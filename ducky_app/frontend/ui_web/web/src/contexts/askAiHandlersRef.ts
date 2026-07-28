import type { ChatTab } from "../types/panel";

export interface AskAiPayload {
  text: string;
  filePath: string;
  startLine: number;
  endLine: number;
}

export interface AskAiHandlers {
  chats: ChatTab[];
  onAsk: (chatId: string, payload: AskAiPayload) => void;
  /** Open create-ducky modal; after create, autofill the composer with the selection draft. */
  onAskNew?: (payload: AskAiPayload) => void;
}

let handlers: AskAiHandlers | null = null;

export function setAskAiHandlers(next: AskAiHandlers | null): void {
  handlers = next;
}

export function getAskAiHandlers(): AskAiHandlers | null {
  return handlers;
}

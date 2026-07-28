import type { ProblemsDraftPayload } from "../utils/formatProblemsDraft";

export interface ProblemsDuckyChat {
  id: string;
  name: string;
  duckyStyle?: string;
}

export interface ProblemsDuckyHandlers {
  chats: ProblemsDuckyChat[];
  onSend: (chatId: string, payload: ProblemsDraftPayload) => void;
  onCreateAndSend: (payload: ProblemsDraftPayload) => void | Promise<void>;
}

let handlers: ProblemsDuckyHandlers | null = null;

export function setProblemsDuckyHandlers(next: ProblemsDuckyHandlers | null): void {
  handlers = next;
}

export function getProblemsDuckyHandlers(): ProblemsDuckyHandlers | null {
  return handlers;
}

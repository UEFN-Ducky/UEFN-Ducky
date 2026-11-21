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


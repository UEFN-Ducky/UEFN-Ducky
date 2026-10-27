import type { ChatMessage } from "../types/panel";
import { callOverHttp, canCallOverHttp } from "./panelHttp";
import { getApi } from "./usePanelApi";

/** Longest a chat load may take before the pane stops waiting and offers Retry. */
export const CHAT_LOAD_TIMEOUT_MS = 45_000;

export class ChatLoadTimeout extends Error {
  constructor() {
    super("timed out");
    this.name = "ChatLoadTimeout";
  }
}

/** `promise`, or a ChatLoadTimeout once `ms` pass without it settling. */
export function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new ChatLoadTimeout()), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (err: unknown) => {
        window.clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * A chat's rows. In the desktop app a long chat is megabytes of JSON; pywebview hands
 * every reply back as one injected script, and a reply that size can stall the page
 * until the WebView is reloaded, so the chat never loads. The page is served by the same
 * local server that answers `/__panel_api`, so ask over plain HTTP first and keep the
 * bridge as the fallback (dev server, or a build without the route).
 */
export async function fetchChatRows(chatId: string, timeoutMs = CHAT_LOAD_TIMEOUT_MS, limit?: number): Promise<ChatMessage[]> {
  const api = getApi();
  if (!api) return [];
  if (canCallOverHttp()) {
    const http = await callOverHttp("load_messages", limit ? [chatId, null, limit] : [chatId], timeoutMs);
    if (http.ok && Array.isArray(http.result)) return http.result as ChatMessage[];
    if (!http.ok && http.timedOut) throw new ChatLoadTimeout();
    // Any other failure: fall through to the bridge.
  }
  const rows: unknown = await withTimeout(limit ? api.load_messages(chatId, null, limit) : api.load_messages(chatId), timeoutMs);
  // On a phone a relay blip or a 502 answers with nothing at all: that is a failed load
  // (the chat says so and retries), never an empty or missing list to render.
  if (!Array.isArray(rows)) throw new Error("no messages came back (the connection dropped)");
  return rows as ChatMessage[];
}

const reported = new Set<string>();

/** True the first time this chat hits this load problem in the session. The pane
 *  reloads after every turn, so one broken chat sent chat_failed 25 times a day from
 *  one PC (Oct 26 2026) and hid how many chats really fail. */
export function firstLoadReport(source: string, chatId: string): boolean {
  const key = `${source}:${chatId}`;
  if (reported.has(key)) return false;
  reported.add(key);
  return true;
}

/** Rows rendered from a reload that fetched only the newest `limit` rows: what is shown
 *  up to where that page starts, then the page (row ids are stable while a chat only
 *  grows). Null when they don't line up (rewound, compacted, or the page starts before
 *  anything shown): the caller loads the whole chat. */
export function spliceTail(shown: ChatMessage[], page: ChatMessage[], limit: number): ChatMessage[] | null {
  if (page.length < limit) return page; // the page is the whole chat
  const first = page[0];
  const at = shown.findIndex((m) => m.id === first.id);
  if (at < 0 || shown[at].role !== first.role || (shown[at].text ?? "") !== (first.text ?? "")) return null;
  return [...shown.slice(0, at), ...page];
}

import type { ChatMessage } from "../types/panel";
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
export async function fetchChatRows(chatId: string, timeoutMs = CHAT_LOAD_TIMEOUT_MS): Promise<ChatMessage[]> {
  const api = getApi();
  if (!api) return [];
  if (typeof window !== "undefined" && window.pywebview && window.location.protocol.startsWith("http")) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch("/__panel_api/load_messages", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ args: [chatId] }),
        signal: controller.signal,
      });
      if (res.ok) {
        const json = (await res.json()) as { ok?: boolean; result?: unknown };
        if (json.ok !== false && Array.isArray(json.result)) return json.result as ChatMessage[];
      }
    } catch {
      if (controller.signal.aborted) throw new ChatLoadTimeout();
      // Any other failure: fall through to the bridge.
    } finally {
      window.clearTimeout(timer);
    }
  }
  return withTimeout(api.load_messages(chatId), timeoutMs);
}

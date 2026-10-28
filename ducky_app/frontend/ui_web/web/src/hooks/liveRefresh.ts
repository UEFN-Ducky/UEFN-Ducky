import type { AgentEvent } from "../types/panel";
import { subscribeAgentEvents } from "./useAgentEventBus";

export const LIVE_REFRESH_DEBOUNCE_MS = 1500;
export const LIVE_REFRESH_MAX_WAIT_MS = 5000;

/**
 * Runs `refresh` once agent events that `accept` lets through go quiet for 1.5 s, and at
 * most 5 s after the first of them, so hundreds of busy agents never starve it. A hidden
 * window refetches nothing: it catches up once when it is shown again.
 */
export function subscribeLiveRefresh(accept: (event: AgentEvent) => boolean, refresh: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let firstAt = 0;
  let missed = false;
  const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
  const fire = () => {
    timer = null;
    firstAt = 0;
    if (hidden()) missed = true;
    else refresh();
  };
  const unsubscribe = subscribeAgentEvents((event) => {
    if (!accept(event)) return;
    if (hidden()) {
      missed = true;
      return;
    }
    const now = Date.now();
    if (!firstAt) firstAt = now;
    if (timer) clearTimeout(timer);
    timer = setTimeout(fire, Math.max(0, Math.min(LIVE_REFRESH_DEBOUNCE_MS, firstAt + LIVE_REFRESH_MAX_WAIT_MS - now)));
  });
  const onVisibility = () => {
    if (hidden() || !missed) return;
    missed = false;
    refresh();
  };
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    if (timer) clearTimeout(timer);
    unsubscribe();
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

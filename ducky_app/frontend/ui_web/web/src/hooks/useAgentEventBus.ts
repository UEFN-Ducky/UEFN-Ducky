import { useEffect, type DependencyList } from "react";
import type { AgentEvent, PanelPushEvent } from "../types/panel";
import { installPerfMonitor, noteFrameDelivery, notePendingDepth } from "./perfMonitor";

/** PanelApi._push_panel events share the HTTP bus — do not treat as agent stream. */
const PANEL_PUSH_TYPES = new Set<string>([
  "key_test_done",
  "key_test_progress",
  "appearance_changed",
  "project_changed",
  "discord_changed",
  "uefn_plugins_changed",
  "models_updated",
  "coding_agents_updated",
  "uefn_plugin_trust_request",
  "browser_pane_state",
  "browser_pane_new_window",
]);

type AgentEventListener = (event: AgentEvent) => void;

const listeners = new Set<AgentEventListener>();
const pendingEvents: AgentEvent[] = [];
const MAX_EVENTS_PER_FRAME = 200;
let deliveryScheduled = false;

export function coalesceAgentEvents(events: AgentEvent[]): AgentEvent[] {
  const out: AgentEvent[] = [];
  for (const event of events) {
    const previous = out[out.length - 1];
    const sameRun =
      previous != null &&
      previous.conv_id === event.conv_id &&
      previous.run_id === event.run_id;
    if (
      sameRun &&
      (event.type === "text_delta" || event.type === "thinking") &&
      previous.type === event.type
    ) {
      out[out.length - 1] = { ...previous, text: (previous.text ?? "") + (event.text ?? "") };
      continue;
    }
    if (sameRun && event.type === "status" && previous.type === "status") {
      out[out.length - 1] = event;
      continue;
    }
    out.push(event);
  }
  return out;
}

function deliver(event: AgentEvent) {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch {
      // ignore subscriber errors
    }
  }
}

function scheduleDelivery() {
  if (deliveryScheduled) return;
  deliveryScheduled = true;
  const schedule =
    typeof window.requestAnimationFrame === "function"
      ? window.requestAnimationFrame.bind(window)
      : (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0);
  schedule(() => {
    deliveryScheduled = false;
    const t0 = performance.now();
    const batch = coalesceAgentEvents(pendingEvents.splice(0, MAX_EVENTS_PER_FRAME));
    for (const event of batch) deliver(event);
    const deliveryMs = performance.now() - t0;
    noteFrameDelivery(deliveryMs, batch.length, pendingEvents.length);
    if (pendingEvents.length > 0) scheduleDelivery();
  });
}

function fanOut(event: AgentEvent) {
  pendingEvents.push(event);
  notePendingDepth(pendingEvents.length);
  scheduleDelivery();
}

let busInstalled = false;
let httpPollStarted = false;
let httpCursor = 0;

const EVENT_POLL_RETRY_MIN_MS = 500;
const EVENT_POLL_RETRY_MAX_MS = 8000;

export function nextEventPollRetryMs(prev: number): number {
  const base = prev > 0 ? prev : EVENT_POLL_RETRY_MIN_MS;
  return Math.min(base * 2, EVENT_POLL_RETRY_MAX_MS);
}

function startHttpEventPoll() {
  if (httpPollStarted) return;
  httpPollStarted = true;
  const poll = async () => {
    let retryMs = EVENT_POLL_RETRY_MIN_MS;
    while (httpPollStarted) {
      try {
        const response = await fetch(`/__panel_events?since=${httpCursor}`, {
          cache: "no-store",
        });
        if (response.status === 403 && window.parent !== window) {
          window.parent.postMessage({ type: "ud-remote-gone" }, "*");
          httpPollStarted = false;
          return;
        }
        if (!response.ok) throw new Error(`event poll ${response.status}`);
        const body = (await response.json()) as { cursor?: number; events?: AgentEvent[] };
        if (typeof body.cursor === "number") httpCursor = body.cursor;
        retryMs = EVENT_POLL_RETRY_MIN_MS;
        if (Array.isArray(body.events)) {
          for (const event of body.events) {
            const kind = String(event?.type || "");
            if (PANEL_PUSH_TYPES.has(kind)) {
              try {
                window.__uefnPanelPush?.(event as unknown as PanelPushEvent);
              } catch {
                /* ignore */
              }
              continue;
            }
            fanOut(event);
          }
        }
      } catch {
        await new Promise<void>((resolve) => window.setTimeout(resolve, retryMs));
        retryMs = nextEventPollRetryMs(retryMs);
      }
    }
  };
  void poll();
}

export function installAgentEventBus() {
  if (busInstalled) return;
  busInstalled = true;
  installPerfMonitor();
  // Agent streaming + Store panel pushes use same-origin long polling instead of
  // evaluate_js. This avoids deadlocks with pywebview's own API-return calls
  // (Settings stuck on Uninstalling… / install progress frozen).
  startHttpEventPoll();
  const queue = (window as Window & { __uefnEventQueue?: AgentEvent[] }).__uefnEventQueue;
  window.__uefnPushEvent = fanOut;
  if (queue?.length) {
    for (const event of queue) {
      fanOut(event);
    }
    (window as Window & { __uefnEventQueue?: AgentEvent[] }).__uefnEventQueue = [];
  }
}

export function subscribeAgentEvents(listener: AgentEventListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Inject a UI-local event (e.g. discovered external file delete) into the same bus
 * the backend push path uses — so tab/diagnostics listeners react identically. */
export function pushLocalAgentEvent(event: AgentEvent): void {
  installAgentEventBus();
  fanOut(event);
}

export function useAgentEventBus() {
  useEffect(() => {
    installAgentEventBus();
  }, []);
}

export function useAgentEventSubscription(
  convId: string,
  handler: AgentEventListener,
  deps: DependencyList = [],
) {
  useEffect(() => {
    installAgentEventBus();
    const wrapped: AgentEventListener = (event) => {
      if (event.conv_id && event.conv_id !== convId && event.type !== "linked_agent") return;
      if (event.type === "linked_agent" && event.parent_conv_id !== convId) return;
      handler(event);
    };
    return subscribeAgentEvents(wrapped);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, ...deps]);
}

import { useEffect, type DependencyList } from "react";
import type { AgentEvent, PanelPushEvent } from "../types/panel";
import { installPerfMonitor, noteFrameDelivery, notePendingDepth } from "./perfMonitor";
import { getDirectTransport } from "../remote/directTransport";
import { isRemote } from "./usePanelApi";
import { openHttpsOnThisDevice } from "../remote/openHttps";

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
  "browser_tab_command",
  "background_job",
  "graphs_changed",
  "graph_focus",
  "templates_changed",
  // Every window's plugin panels and scope bars listen for these on the panel bus;
  // left on the agent bus they reached no one (main window or floating).
  "plugin_scope_changed",
  // Live groups (backend/uefn_plugins/live.py): a teammate's message, who is live, what
  // teammates hold, a groupmate's live state, the group's shared values, and who is live
  // where on a team copy (the title bar's Live menu).
  "live_message",
  "live_peers",
  "live_holds",
  "live_member_state",
  "live_shared",
  "live_team",
  "live_invite",
  "duckyos_account_changed",
  // A plugin's api.emit_hook / api.set_appearance_profile; the main window acts on them.
  "plugin_hook",
  "appearance_profile_requested",
  "open_target",
]);

type AgentEventListener = (event: AgentEvent) => void;

const listeners = new Set<AgentEventListener>();
/** Called as each event arrives instead of on the next frame. */
const immediateListeners = new Set<AgentEventListener>();
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
  if (String(event.type) === "open_url" && isRemote()) {
    openHttpsOnThisDevice(String(event.url || ""));
    return;
  }
  for (const listener of immediateListeners) {
    try {
      listener(event);
    } catch {
      // ignore subscriber errors
    }
  }
  pendingEvents.push(event);
  notePendingDepth(pendingEvents.length);
  scheduleDelivery();
}

let busInstalled = false;
let httpPollStarted = false;
let httpCursor = 0;
/** Newest server event when this page first polled; the backlog up to it is history. */
let replayUntil = -1;

/**
 * How many events at the start of a poll batch were already in the backlog when this
 * page loaded. A batch is contiguous and ends at `cursor`.
 */
export function replayedCount(cursor: number, count: number, until: number): number {
  const first = cursor - count + 1;
  return Math.max(0, Math.min(count, until - first + 1));
}

const EVENT_POLL_RETRY_MIN_MS = 500;
const EVENT_POLL_RETRY_MAX_MS = 8000;

export function nextEventPollRetryMs(prev: number): number {
  const base = prev > 0 ? prev : EVENT_POLL_RETRY_MIN_MS;
  return Math.min(base * 2, EVENT_POLL_RETRY_MAX_MS);
}

/** Stale `remote_gone` in catch-up must not kick a new overlay iframe. */
export function remoteGoneIsLive(catchUpDone: boolean, kind: string): boolean {
  return catchUpDone && kind === "remote_gone";
}

/** A plugin's own push (``api.emit``-style events carrying its ``plugin_id``, such as
 * Kanban's ``kanban_changed``): its panels subscribe to it on the panel bus
 * (``plugin.subscribe``), so it goes there too, as well as to the agent bus. */
function pluginPush(event: AgentEvent): void {
  if (typeof (event as { plugin_id?: unknown }).plugin_id !== "string") return;
  try {
    window.__uefnPanelPush?.(event as unknown as PanelPushEvent);
  } catch {
    /* ignore */
  }
}

function startHttpEventPoll() {
  if (httpPollStarted) return;
  httpPollStarted = true;
  const poll = async () => {
    let retryMs = EVENT_POLL_RETRY_MIN_MS;
    let catchUpDone = false;
    while (httpPollStarted) {
      try {
        const response = await fetch(`/__panel_events?since=${httpCursor}`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`event poll ${response.status}`);
        const body = (await response.json()) as { cursor?: number; events?: AgentEvent[]; head?: number };
        if (replayUntil < 0) replayUntil = typeof body.head === "number" ? body.head : 0;
        const history =
          typeof body.cursor === "number" && Array.isArray(body.events)
            ? replayedCount(body.cursor, body.events.length, replayUntil)
            : 0;
        if (typeof body.cursor === "number") httpCursor = body.cursor;
        retryMs = EVENT_POLL_RETRY_MIN_MS;
        if (Array.isArray(body.events)) {
          for (const [index, raw] of body.events.entries()) {
            // Turns that ended before this page loaded must not look like live runs.
            // Live ones carry their arrival time: a minimized window delivers them later
            // in one batch, and the turn clock read their start and stop as "Took 0ms".
            const event = index < history ? { ...raw, replayed: true } : { ...raw, received_at: Date.now() };
            const kind = String(event?.type || "");
            if (remoteGoneIsLive(catchUpDone, kind) && window.parent !== window) {
              window.parent.postMessage({ type: "ud-remote-gone" }, "*");
              httpPollStarted = false;
              return;
            }
            if (PANEL_PUSH_TYPES.has(kind)) {
              try {
                window.__uefnPanelPush?.(event as unknown as PanelPushEvent);
              } catch {
                /* ignore */
              }
              continue;
            }
            pluginPush(event);
            fanOut(event);
          }
        }
        catchUpDone = true;
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
  // Direct Remote View (phone → PC over WebRTC): events arrive on the
  // `events` DataChannel; there is no panel server on this origin to poll.
  const direct = getDirectTransport();
  if (direct) {
    direct.onEvent((arrived) => {
      const event = { ...arrived, received_at: Date.now() };
      const kind = String(event?.type || "");
      if (PANEL_PUSH_TYPES.has(kind)) {
        try {
          window.__uefnPanelPush?.(event as unknown as PanelPushEvent);
        } catch {
          /* ignore */
        }
        return;
      }
      pluginPush(event);
      fanOut(event);
    });
  } else {
    startHttpEventPoll();
  }
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

/** Like subscribeAgentEvents, but each event as it arrives, uncoalesced. A minimized or
 * hidden window paints no frames, so frame-batched listeners hear nothing until it is
 * shown again; Remote Ducky answers the phone and streams to it from this one. */
export function subscribeAgentEventsImmediate(listener: AgentEventListener): () => void {
  immediateListeners.add(listener);
  return () => immediateListeners.delete(listener);
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

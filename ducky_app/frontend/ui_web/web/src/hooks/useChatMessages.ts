import { useCallback, useEffect, useReducer, useRef } from "react";

import type { AgentEvent, MessageAttachmentDto } from "../types/panel";

import { getCachedChatMessages, setCachedChatMessages } from "./chatMessagesCache";
import { getApi } from "./usePanelApi";
import { useAgentEventSubscription } from "./useAgentEventBus";
import {
  chatRunReducer,
  initialRunState,
  isRunActive,
  type RunState,
} from "./chatRun/chatRunReducer";
import { markChatTurnIdle, markChatTurnRunning } from "./chatTurnTimer";
import { prefixSpeaker } from "../utils/agentActivity";

/**
 * The one chat message pipeline — a thin controller around {@link chatRunReducer}.
 *
 *   backend load_messages(convId) ──► dispatch(loaded) ──┐
 *   agent events ──► dispatch(agentEvent) ───────────────┤
 *   send / stop / scroll ──► dispatch(...) ──────────────┼─► chatRunReducer ─► state
 *   running-set poll ──► dispatch(reconcile) ────────────┘         │
 *                                                                  └─► cache (instant tab restore)
 *
 * ALL run + message-tail state lives in the reducer. This hook only does I/O:
 * fetch the conversation, subscribe to the event bus, poll the backend to un-stick
 * a stalled run, and mirror the view into the cache. Every result is turned back
 * into a reducer action, so there is exactly one place that decides what the run
 * is doing. The public API returned here is unchanged, so ChatPane is untouched.
 */

/** How long a run must stay silent AND absent from the global running-set before
 *  we do one authoritative liveness check. Must comfortably exceed the
 *  useRunningAgents poll interval so a live-but-quiet run is confirmed first. */
const RECONCILE_GRACE_MS = 15000;

/** Seed reducer state from the in-memory cache so a tab switch restores instantly.
 *  ChatPane is keyed by chat.id, so chatId is constant for the hook's lifetime and
 *  this runs exactly once per mount. */
function initFromCache(arg: { chatId: string; externalRunning: boolean }): RunState {
  const cached = getCachedChatMessages(arg.chatId);
  if (!cached) return { ...initialRunState, externalRunning: arg.externalRunning };
  return {
    ...initialRunState,
    externalRunning: arg.externalRunning,
    messages: cached.messages,
    stream: cached.streamBuffer,
    thinking: cached.streamThinking ?? "",
    status: cached.optimisticRunning ? "running" : "idle",
    runId: cached.activeRunId,
    stopped: cached.stoppedRun,
    hasNewBelow: cached.hasNewBelow,
    atBottom: cached.isAtBottom,
  };
}

export function useChatMessages(chatId: string, visible: boolean, isAgentRunning: boolean) {
  const [state, dispatch] = useReducer(chatRunReducer, { chatId, externalRunning: isAgentRunning }, initFromCache);

  // A ref mirror so visibility/mount effects can read the latest run state without
  // re-subscribing on every keystroke of the stream.
  const stateRef = useRef(state);
  stateRef.current = state;

  const loadSeqRef = useRef(0);

  // The single load path: fetch the whole conversation, hand it to the reducer,
  // which decides merge-vs-replace based on whether a run is in flight.
  const load = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    const seq = ++loadSeqRef.current;
    const rows = await api.load_messages(chatId);
    if (seq !== loadSeqRef.current) return; // a newer load superseded this one
    dispatch({ type: "loaded", rows });
  }, [chatId]);

  // Keep the reducer's mirror of the global running-set in sync with the prop.
  useEffect(() => {
    dispatch({ type: "externalRunning", running: isAgentRunning });
  }, [isAgentRunning]);

  // Latch the shared turn clock as soon as this pane knows a run is live
  // (covers the gap before the first agent event hits the global running-set).
  const runActive = isRunActive(state);
  useEffect(() => {
    if (runActive) markChatTurnRunning(chatId);
    else markChatTurnIdle(chatId);
  }, [chatId, runActive]);

  // Fetch the conversation once on mount (even while hidden, so a background pane
  // is ready). A run in flight keeps its optimistic tail; loaded() merges.
  useEffect(() => {
    void load();
  }, [load]);

  // Safety-net refresh when the pane (re)appears while idle and pinned to the
  // bottom — catches anything missed while hidden without yanking a scrolled-up
  // reader. Fires only on the false→true visibility edge.
  const wasVisibleRef = useRef(visible);
  useEffect(() => {
    const becameVisible = visible && !wasVisibleRef.current;
    wasVisibleRef.current = visible;
    const s = stateRef.current;
    if (becameVisible && s.status === "idle" && s.atBottom && !s.stopped) {
      void load();
    }
  }, [visible, load]);

  // Command channel: the reducer bumps reloadToken whenever a transition wants the
  // canonical backend copy pulled (context change, run finished at bottom,
  // cancel, reconcile clear). Only act on increments, not the initial value.
  const lastReloadRef = useRef(state.reloadToken);
  useEffect(() => {
    if (state.reloadToken === lastReloadRef.current) return;
    lastReloadRef.current = state.reloadToken;
    void load();
  }, [state.reloadToken, load]);

  // Reconcile a run we believe is streaming against the backend — the safety net
  // for a missed agent_stopped event or a stale cache after remount.
  //
  // We lean on the ONE global running-set poller (useRunningAgents) instead of a
  // second continuous poll: this effect only arms while status === "running" AND
  // the global set doesn't list this chat. Any activity event (text/thinking/tool)
  // or the global poll re-adds the chat → isAgentRunning flips true → this effect
  // tears the timer down, so a live run is never touched. Only if the chat stays
  // silent and unlisted past a full poll cycle do we make ONE authoritative check
  // and, if it's really gone, clear. The "sending" state is excluded entirely so a
  // freshly sent run (whose worker thread may still be spinning up) is never a
  // candidate here.
  useEffect(() => {
    if (state.status !== "running" || isAgentRunning) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const api = getApi();
      if (!api?.list_running_agents) {
        if (!cancelled) dispatch({ type: "reconcile", running: false });
        return;
      }
      const ids = await api.list_running_agents();
      if (cancelled) return;
      dispatch({ type: "reconcile", running: ids.includes(chatId) });
    }, RECONCILE_GRACE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [chatId, state.status, isAgentRunning]);

  // Mirror the live view into the cache so a tab switch / hidden→visible pane
  // restores instantly (the backend file stays the source of truth).
  useEffect(() => {
    setCachedChatMessages(chatId, {
      messages: state.messages,
      streamBuffer: state.stream,
      streamThinking: state.thinking,
      optimisticRunning: state.status !== "idle",
      hasNewBelow: state.hasNewBelow,
      isAtBottom: state.atBottom,
      activeRunId: state.runId,
      stoppedRun: state.stopped,
    });
  }, [
    chatId,
    state.messages,
    state.stream,
    state.thinking,
    state.status,
    state.hasNewBelow,
    state.atBottom,
    state.runId,
    state.stopped,
  ]);

  // Single live path: every agent event for this chat becomes one reducer action.
  // dispatch is stable, so this subscription is installed once and never churns.
  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.conv_id && event.conv_id !== chatId) return;
      dispatch({ type: "agentEvent", event });
    },
    [chatId],
  );
  useAgentEventSubscription(chatId, onEvent, [onEvent]);

  const appendUserMessage = useCallback((text: string, attachments?: MessageAttachmentDto[]) => {
    dispatch({ type: "send", text, attachments });
  }, []);

  const rewindAndAppendUser = useCallback((text: string, attachments?: MessageAttachmentDto[]) => {
    dispatch({ type: "resend", text, attachments });
  }, []);

  const setActiveRunId = useCallback((runId: string) => {
    dispatch({ type: "sendAccepted", runId });
  }, []);

  const invalidateActiveRun = useCallback(() => {
    dispatch({ type: "invalidate" });
  }, []);

  const onAtBottomChange = useCallback((atBottom: boolean) => {
    dispatch({ type: "atBottom", atBottom });
  }, []);

  const clearStream = useCallback(() => {
    dispatch({ type: "clearStream" });
  }, []);

  const stopRun = useCallback(() => {
    dispatch({ type: "stop" });
  }, []);

  const stopOptimistic = useCallback(() => {
    dispatch({ type: "stopOptimistic" });
  }, []);

  return {
    messages: state.messages,
    streamBuffer: state.stream,
    streamThinking: state.thinking,
    streamStatus: prefixSpeaker(state.statusAuthor, state.statusText),
    agentRunning: isRunActive(state),
    optimisticRunning: state.status !== "idle",
    hasNewBelow: state.hasNewBelow,
    isAtBottom: state.atBottom,
    reloadMessages: load,
    appendUserMessage,
    rewindAndAppendUser,
    setActiveRunId,
    invalidateActiveRun,
    onAtBottomChange,
    clearStream,
    stopOptimistic,
    stopRun,
  };
}

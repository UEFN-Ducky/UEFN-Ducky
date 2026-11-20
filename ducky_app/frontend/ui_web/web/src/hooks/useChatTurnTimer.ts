import { useSyncExternalStore } from "react";
import { useNow } from "../utils/sharedClock";
import {
  chatTurnElapsedMs,
  formatElapsedMs,
  getChatTurnTimer,
  getChatTurnTimersVersion,
  subscribeChatTurnTimers,
  type ChatTurnTimer,
} from "./chatTurnTimer";

const TICK_MS = 250;

function subscribe(listener: () => void) {
  return subscribeChatTurnTimers(listener);
}

function getVersion() {
  return getChatTurnTimersVersion();
}

export type ChatTurnTimerView = {
  timer: ChatTurnTimer | null;
  /** Elapsed milliseconds (live or final). null if this chat has never run. */
  ms: number | null;
  running: boolean;
  /** Formatted elapsed string, or null when unused. */
  label: string | null;
};

type ChatTurnTimerState = "none" | "running" | "done";

function turnTimerState(chatId: string): ChatTurnTimerState {
  const timer = getChatTurnTimer(chatId);
  if (!timer) return "none";
  return timer.endedAt == null ? "running" : "done";
}

/**
 * Whether this chat has a turn clock and whether it runs, without the 250 ms tick.
 * For a whole chat pane: ticking it re-rendered the pane four times a second for every
 * running chat, hidden tabs included. ElapsedTimer draws the clock itself.
 */
export function useChatTurnTimerState(chatId: string): { hasTimer: boolean; running: boolean } {
  const read = () => turnTimerState(chatId);
  const state = useSyncExternalStore(subscribe, read, read);
  return { hasTimer: state !== "none", running: state === "running" };
}

/**
 * Live-updating turn duration for a chat. Ticks while running; freezes when idle
 * so the last turn's time stays visible.
 */
export function useChatTurnTimer(chatId: string): ChatTurnTimerView {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  const timer = getChatTurnTimer(chatId);
  const running = Boolean(timer && timer.endedAt == null);
  // Every running chat's clock moves on the same tick (utils/sharedClock).
  const now = useNow(TICK_MS, running);

  const ms = chatTurnElapsedMs(timer, running ? now : undefined);
  return {
    timer,
    ms,
    running,
    label: ms == null ? null : formatElapsedMs(ms),
  };
}

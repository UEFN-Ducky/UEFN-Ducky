import { useEffect, useState, useSyncExternalStore } from "react";
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

/**
 * Live-updating turn duration for a chat. Ticks while running; freezes when idle
 * so the last turn's time stays visible.
 */
export function useChatTurnTimer(chatId: string): ChatTurnTimerView {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  const timer = getChatTurnTimer(chatId);
  const running = Boolean(timer && timer.endedAt == null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [running, timer?.startedAt]);

  const ms = chatTurnElapsedMs(timer, running ? now : undefined);
  return {
    timer,
    ms,
    running,
    label: ms == null ? null : formatElapsedMs(ms),
  };
}

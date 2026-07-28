import { useEffect, useState } from "react";
import { getApi } from "../hooks/usePanelApi";

export type TerminalRunner = "mcp" | "user";

export interface TerminalBusyStatus {
  running: boolean;
  runner: TerminalRunner | null;
}

const POLL_MS = 2000;

function deriveRunner(busy?: boolean, running?: boolean): TerminalRunner | null {
  if (!running) return null;
  return busy ? "mcp" : "user";
}

export function useTerminalBusyStatuses(sessionIds: string[]): Map<string, TerminalBusyStatus> {
  const [statuses, setStatuses] = useState<Map<string, TerminalBusyStatus>>(() => new Map());
  const idsKey = sessionIds.filter(Boolean).sort().join("\0");

  useEffect(() => {
    const ids = idsKey ? idsKey.split("\0") : [];
    if (ids.length === 0) {
      setStatuses(new Map());
      return;
    }

    let cancelled = false;

    const poll = async () => {
      const api = getApi();
      if (!api || cancelled) return;
      const entries = await Promise.all(
        ids.map(async (sessionId) => {
          const state = await api.terminal_busy(sessionId).catch(() => null);
          if (!state?.ok) return [sessionId, { running: false, runner: null }] as const;
          const running = !!state.running;
          return [
            sessionId,
            { running, runner: deriveRunner(state.busy, running) },
          ] as const;
        }),
      );
      if (cancelled) return;
      setStatuses(new Map(entries));
    };

    void poll();
    const timer = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [idsKey]);

  return statuses;
}

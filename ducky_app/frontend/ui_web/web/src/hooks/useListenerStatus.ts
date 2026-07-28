import { useEffect, useRef, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "./onApiReady";
import type { ListenerStatus, PanelApi } from "../types/panel";

const OFFLINE: ListenerStatus = { online: false, version: "…" };

type StatusKey = "online" | "wedged" | "offline";

function statusKey(status: ListenerStatus): StatusKey {
  if (status.wedged) return "wedged";
  if (status.online) return "online";
  return "offline";
}

/** Require consecutive bad polls before leaving online or switching wedged/offline. */
function settleListenerStatus(prev: ListenerStatus, next: ListenerStatus, streak: { key: StatusKey; count: number }) {
  const nextKey = statusKey(next);
  if (nextKey === "online") {
    streak.key = "online";
    streak.count = 0;
    return next;
  }

  if (nextKey === streak.key) {
    streak.count += 1;
  } else {
    streak.key = nextKey;
    streak.count = 1;
  }

  if (statusKey(prev) === "online" && streak.count < 2) {
    return prev;
  }
  if (statusKey(prev) !== "online" && nextKey !== statusKey(prev) && streak.count < 2) {
    return prev;
  }

  return next;
}

function applyStatus(
  next: ListenerStatus,
  stableRef: { current: ListenerStatus },
  streakRef: { current: { key: StatusKey; count: number } },
  setStatus: (s: ListenerStatus) => void,
) {
  const settled = settleListenerStatus(stableRef.current, next, streakRef.current);
  stableRef.current = settled;
  setStatus(settled);
}

export function useListenerStatus(pollMs = 8000, refreshToken = 0) {
  const [status, setStatus] = useState<ListenerStatus>(OFFLINE);
  const apiRef = useRef<PanelApi | null>(null);
  const stableRef = useRef<ListenerStatus>(OFFLINE);
  const streakRef = useRef<{ key: StatusKey; count: number }>({ key: "offline", count: 0 });
  const inFlightRef = useRef(false);

  useEffect(() => {
    let pollId: number | undefined;
    let started = false;

    const cleanupWait = onApiReady((api) => {
      if (started) return;
      started = true;
      apiRef.current = api;

      const poll = () => {
        if (inFlightRef.current) return;
        if (typeof api.get_listener_status !== "function") return;
        inFlightRef.current = true;
        void api
          .get_listener_status()
          .then((next) => {
            applyStatus(next, stableRef, streakRef, setStatus);
          })
          .catch(() => {
            void api.get_version().then((version) => {
              applyStatus({ online: false, version }, stableRef, streakRef, setStatus);
            });
          })
          .finally(() => {
            inFlightRef.current = false;
          });
      };

      poll();
      pollId = window.setInterval(poll, pollMs);
    });

    return () => {
      cleanupWait();
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [pollMs]);

  useEffect(() => {
    if (refreshToken === 0) return;
    const api = apiRef.current ?? getApi();
    if (!api || typeof api.get_listener_status !== "function" || inFlightRef.current) return;

    inFlightRef.current = true;
    void api
      .get_listener_status()
      .then((next) => {
        applyStatus(next, stableRef, streakRef, setStatus);
      })
      .catch(() => {
        void api.get_version().then((version) => {
          applyStatus({ online: false, version }, stableRef, streakRef, setStatus);
        });
      })
      .finally(() => {
        inFlightRef.current = false;
      });
  }, [refreshToken]);

  return status;
}

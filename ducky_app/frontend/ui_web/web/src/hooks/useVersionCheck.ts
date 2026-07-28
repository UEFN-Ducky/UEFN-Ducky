import { useCallback, useEffect, useRef, useState } from "react";
import { onApiReady } from "./onApiReady";
import { getApi } from "./usePanelApi";
import type { AppUpdateStatus } from "../types/panel";

const POLL_MS = 600_000; // 10 minutes

export function useVersionCheck() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const inFlightRef = useRef(false);

  const refresh = useCallback(() => {
    const api = getApi();
    if (inFlightRef.current || typeof api?.get_app_update_status !== "function") return;
    inFlightRef.current = true;
    void api
      .get_app_update_status()
      .then((next) => {
        setStatus(next);
      })
      .catch(() => {})
      .finally(() => {
        inFlightRef.current = false;
      });
  }, []);

  useEffect(() => {
    let pollId: number | undefined;
    let started = false;

    const cleanupWait = onApiReady(() => {
      if (started) return;
      started = true;
      refresh();
      pollId = window.setInterval(refresh, POLL_MS);
    });

    return () => {
      cleanupWait();
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [refresh]);

  const showModal = !!status?.update_available && status.channel === "installed" && !dismissed;

  return {
    status,
    showModal,
    refresh,
    dismiss: () => setDismissed(true),
  };
}

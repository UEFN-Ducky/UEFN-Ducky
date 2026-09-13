import { useEffect, useRef } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import type { ProjectFileStat } from "../types/panel";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";

const DEFAULT_POLL_MS = 1000;

function fingerprint(stat: ProjectFileStat): string {
  return `${stat.exists}:${stat.mtime_ns}:${stat.size}`;
}

/** Polls disk mtime/size and fires when the file changes externally. */
export function useWatchProjectFile(
  relativePath: string,
  onChange: (stat: ProjectFileStat) => void,
  options?: { enabled?: boolean; pollMs?: number },
): void {
  const enabled = options?.enabled ?? true;
  const pollMs = options?.pollMs ?? DEFAULT_POLL_MS;
  const fpRef = useRef<string | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!enabled || !relativePath) return;

    let cancelled = false;
    let inFlight = false;
    let stopPoll: (() => void) | undefined;

    const poll = async () => {
      const api = getApi();
      if (!api?.stat_project_file || cancelled || inFlight) return;
      inFlight = true;
      try {
        const stat = await api.stat_project_file(relativePath);
        if (cancelled) return;
        const key = fingerprint(stat);
        if (fpRef.current === null) {
          fpRef.current = key;
          return;
        }
        if (key !== fpRef.current) {
          fpRef.current = key;
          onChangeRef.current(stat);
        }
      } catch {
        // deleted or transient read error — next poll retries
      } finally {
        inFlight = false;
      }
    };

    const stop = onApiReady(() => {
      fpRef.current = null;
      void poll();
      stopPoll = setVisibleInterval(() => void poll(), pollMs);
    });

    return () => {
      cancelled = true;
      stop();
      stopPoll?.();
      fpRef.current = null;
    };
  }, [relativePath, enabled, pollMs]);
}

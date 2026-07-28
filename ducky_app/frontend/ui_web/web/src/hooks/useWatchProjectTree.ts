import { useEffect, useRef } from "react";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";

const DEFAULT_POLL_MS = 1500;

/**
 * Polls per-directory fingerprints and fires when any watched folder changes on disk
 * (files added/removed/renamed by UEFN, Windows Explorer, git, …). Mirrors
 * useWatchProjectFile but batched across the tree's Content root + expanded folders.
 */
export function useWatchProjectTree(
  dirPaths: readonly string[],
  onChanged: (changed: string[]) => void,
  options?: { enabled?: boolean; pollMs?: number },
): void {
  const enabled = options?.enabled ?? true;
  const pollMs = options?.pollMs ?? DEFAULT_POLL_MS;
  const fpRef = useRef<Map<string, string>>(new Map());
  const pathsRef = useRef(dirPaths);
  pathsRef.current = dirPaths;
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let pollId: number | undefined;

    const poll = async () => {
      const api = getApi();
      const paths = pathsRef.current;
      if (!api?.fingerprint_project_dirs || cancelled || paths.length === 0) return;
      try {
        const { fingerprints } = await api.fingerprint_project_dirs([...paths]);
        if (cancelled) return;
        const changed: string[] = [];
        for (const [path, fp] of Object.entries(fingerprints)) {
          if (!fp) continue; // "" = not a watchable Content dir; ignore
          if (!fpRef.current.has(path)) {
            fpRef.current.set(path, fp); // first sighting = baseline, never a "change"
            continue;
          }
          if (fpRef.current.get(path) !== fp) {
            fpRef.current.set(path, fp);
            changed.push(path);
          }
        }
        if (changed.length) onChangedRef.current(changed);
      } catch {
        // transient read error — next poll retries
      }
    };

    const stop = onApiReady(() => {
      fpRef.current = new Map();
      void poll();
      pollId = window.setInterval(() => void poll(), pollMs);
    });

    return () => {
      cancelled = true;
      stop();
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [enabled, pollMs]);
}

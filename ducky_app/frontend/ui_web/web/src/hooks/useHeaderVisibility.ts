import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "./onApiReady";
import {
  HEADER_VISIBILITY_EVENT,
  HEADER_VISIBILITY_KEY,
  isHeaderButtonVisible,
  normalizeHeaderVisibility,
  persistHeaderVisibility,
  readHeaderVisibility,
  syncLocalHeaderVisibility,
  withHeaderButtonVisible,
  type HeaderVisibilitySnapshot,
} from "../workspace/headerVisibilityStorage";

export function useHeaderVisibility() {
  const [snapshot, setSnapshot] = useState<HeaderVisibilitySnapshot>(readHeaderVisibility);

  useEffect(() => {
    return onApiReady((api) => {
      if (!api.get_header_visibility) return;
      void api.get_header_visibility().then((raw) => {
        if (raw && Array.isArray(raw.hidden)) {
          syncLocalHeaderVisibility(normalizeHeaderVisibility(raw));
          return;
        }
        void api.save_header_visibility?.(readHeaderVisibility());
      });
    });
  }, []);

  useEffect(() => {
    const refresh = () => setSnapshot(readHeaderVisibility());
    const onStorage = (e: StorageEvent) => {
      if (e.key === HEADER_VISIBILITY_KEY) refresh();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(HEADER_VISIBILITY_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(HEADER_VISIBILITY_EVENT, refresh);
    };
  }, []);

  const isVisible = useCallback((id: string) => isHeaderButtonVisible(snapshot, id), [snapshot]);

  const setVisible = useCallback((id: string, visible: boolean) => {
    persistHeaderVisibility(withHeaderButtonVisible(readHeaderVisibility(), id, visible));
  }, []);

  return { snapshot, isVisible, setVisible };
}

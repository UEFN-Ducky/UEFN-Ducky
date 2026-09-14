import { useCallback, useEffect, useState } from "react";
import {
  HEADER_VISIBILITY_EVENT,
  HEADER_VISIBILITY_KEY,
  isHeaderButtonVisible,
  persistHeaderVisibility,
  readHeaderVisibility,
  withHeaderButtonVisible,
  type HeaderVisibilitySnapshot,
} from "../workspace/headerVisibilityStorage";

export function useHeaderVisibility() {
  const [snapshot, setSnapshot] = useState<HeaderVisibilitySnapshot>(readHeaderVisibility);

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

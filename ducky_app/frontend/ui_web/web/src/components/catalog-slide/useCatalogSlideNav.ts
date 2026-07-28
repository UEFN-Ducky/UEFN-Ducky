import { useCallback, useEffect, useState } from "react";
import { CATALOG_DETAIL_SLIDE_MS } from "./types";

/**
 * List ↔ L1 detail slide + local nested focus (L2+).
 * History drills wire selectedKey externally; focusId stays local (Plans parity).
 */
export function useCatalogSlideNav(opts?: { initialKey?: string | null }) {
  const [selectedKey, setSelectedKey] = useState<string | null>(opts?.initialKey ?? null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const detailOpen = selectedKey != null;
  const [detailRendered, setDetailRendered] = useState(detailOpen);

  useEffect(() => {
    if (detailOpen) {
      setDetailRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setDetailRendered(false), CATALOG_DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [detailOpen]);

  const openDetail = useCallback((key: string) => {
    setSelectedKey(key);
    setFocusId(null);
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedKey(null);
    setFocusId(null);
  }, []);

  /** Clear nested focus only (stay on L1 detail). */
  const clearFocus = useCallback(() => {
    setFocusId(null);
  }, []);

  return {
    selectedKey,
    setSelectedKey,
    focusId,
    setFocusId,
    clearFocus,
    detailOpen,
    detailRendered,
    openDetail,
    closeDetail,
  };
}

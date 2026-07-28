import { useCallback, useRef, useState } from "react";

export const SIDEBAR_WIDTH_DEFAULT = 240;
export const SIDEBAR_WIDTH_MIN = 180;
export const SIDEBAR_WIDTH_MAX = 560;
const STORAGE_KEY = "uefn-panel-sidebar-width";

function clampWidth(value: number): number {
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, value));
}

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return SIDEBAR_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return SIDEBAR_WIDTH_DEFAULT;
    return clampWidth(parsed);
  } catch {
    return SIDEBAR_WIDTH_DEFAULT;
  }
}

export function useSidebarWidth() {
  const [width, setWidth] = useState(readStoredWidth);
  const widthRef = useRef(width);
  widthRef.current = width;

  const onResize = useCallback((deltaX: number) => {
    setWidth((prev) => clampWidth(prev + deltaX));
  }, []);

  const persistWidth = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(widthRef.current));
    } catch {
      // ignore storage errors
    }
  }, []);

  return { width, onResize, persistWidth };
}

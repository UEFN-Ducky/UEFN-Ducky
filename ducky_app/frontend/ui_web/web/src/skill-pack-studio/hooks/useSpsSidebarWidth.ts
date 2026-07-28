import { useCallback, useRef, useState } from "react";

export const SPS_SIDEBAR_WIDTH_DEFAULT = 260;
export const SPS_SIDEBAR_WIDTH_MIN = 200;
export const SPS_SIDEBAR_WIDTH_MAX = 480;
const STORAGE_KEY = "uefn-sps-sidebar-width";

function clampWidth(value: number): number {
  return Math.min(SPS_SIDEBAR_WIDTH_MAX, Math.max(SPS_SIDEBAR_WIDTH_MIN, value));
}

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return SPS_SIDEBAR_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return SPS_SIDEBAR_WIDTH_DEFAULT;
    return clampWidth(parsed);
  } catch {
    return SPS_SIDEBAR_WIDTH_DEFAULT;
  }
}

export function useSpsSidebarWidth() {
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

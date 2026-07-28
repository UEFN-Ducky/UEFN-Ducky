import { useCallback, useRef, useState } from "react";

export const OUTLINE_PANEL_WIDTH_DEFAULT = 260;
export const OUTLINE_PANEL_WIDTH_MIN = 180;
export const OUTLINE_PANEL_WIDTH_MAX = 480;
const VERSE_STORAGE_KEY = "uefn-panel-outline-width";
const PLAN_STORAGE_KEY = "uefn-panel-plan-outline-width";

function clampWidth(value: number): number {
  return Math.min(OUTLINE_PANEL_WIDTH_MAX, Math.max(OUTLINE_PANEL_WIDTH_MIN, value));
}

function readStoredWidth(storageKey: string): number {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw === null) return OUTLINE_PANEL_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return OUTLINE_PANEL_WIDTH_DEFAULT;
    return clampWidth(parsed);
  } catch {
    return OUTLINE_PANEL_WIDTH_DEFAULT;
  }
}

/** Width state for a right-side outline sidebar (drag left border). */
export function useOutlinePanelWidth(storageKey: string = VERSE_STORAGE_KEY) {
  const [width, setWidth] = useState(() => readStoredWidth(storageKey));
  const widthRef = useRef(width);
  widthRef.current = width;

  const onResize = useCallback((deltaX: number) => {
    // Panel sits on the right: dragging the left edge left (negative delta) widens it.
    setWidth((prev) => clampWidth(prev - deltaX));
  }, []);

  const persistWidth = useCallback(() => {
    try {
      localStorage.setItem(storageKey, String(widthRef.current));
    } catch {
      // ignore storage errors
    }
  }, [storageKey]);

  return { width, onResize, persistWidth };
}

/** Plan detail steps aside — independent width from Verse outline. */
export function usePlanOutlinePanelWidth() {
  return useOutlinePanelWidth(PLAN_STORAGE_KEY);
}

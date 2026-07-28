import { useCallback, useRef, useState } from "react";

export const SETTINGS_SIDEBAR_WIDTH_DEFAULT = 200;
/** Icon rail — icon + horizontal padding; labels hide below ICONS_ONLY. */
export const SETTINGS_SIDEBAR_WIDTH_MIN = 52;
/** Below this width, nav shows icons only (labels use title tooltips). */
export const SETTINGS_SIDEBAR_ICONS_ONLY_BELOW = 110;
export const SETTINGS_SIDEBAR_WIDTH_MAX = 360;
const STORAGE_KEY = "uefn-settings-sidebar-width";

function clampWidth(value: number): number {
  return Math.min(SETTINGS_SIDEBAR_WIDTH_MAX, Math.max(SETTINGS_SIDEBAR_WIDTH_MIN, value));
}

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return SETTINGS_SIDEBAR_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return SETTINGS_SIDEBAR_WIDTH_DEFAULT;
    return clampWidth(parsed);
  } catch {
    return SETTINGS_SIDEBAR_WIDTH_DEFAULT;
  }
}

export function useSettingsSidebarWidth() {
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

  const iconsOnly = width < SETTINGS_SIDEBAR_ICONS_ONLY_BELOW;
  return { width, iconsOnly, onResize, persistWidth };
}

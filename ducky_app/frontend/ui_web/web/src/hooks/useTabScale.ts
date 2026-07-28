import { useEffect, useLayoutEffect, useRef, useState } from "react";

const STORAGE_KEY = "uefn-editor-tab-scale";
const CHANGE_EVENT = "uefn-editor-tab-scale";

const SCALE_MIN = 0.7;
const SCALE_MAX = 1.6;
const SCALE_DEFAULT = 1;

function clampScale(value: number): number {
  return Math.min(SCALE_MAX, Math.max(SCALE_MIN, value));
}

function readScale(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return SCALE_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return SCALE_DEFAULT;
    return clampScale(parsed);
  } catch {
    return SCALE_DEFAULT;
  }
}

/**
 * Global editor-tab size multiplier, adjusted with Ctrl+wheel over the tab bar.
 * Every tab bar in the app (and across windows) stays in sync via localStorage +
 * a custom event, mirroring the other synced UI-preference hooks.
 */
export function useTabScale(targetRef: React.RefObject<HTMLElement | null>): number {
  const [scale, setScale] = useState(readScale);
  const scaleRef = useRef(scale);
  scaleRef.current = scale;

  // Expose on :root so parent rows (.editor-tab-bar-row) can scale with tabs.
  useLayoutEffect(() => {
    document.documentElement.style.setProperty("--tab-scale", String(scale));
  }, [scale]);

  // Stay in sync with other tab bars (custom event, same window) and other
  // windows (storage event).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setScale(readScale());
    };
    const onCustom = () => setScale(readScale());
    window.addEventListener("storage", onStorage);
    window.addEventListener(CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(CHANGE_EVENT, onCustom);
    };
  }, []);

  // Ctrl+wheel to resize. Native non-passive listener so we can preventDefault
  // (React's onWheel is passive and browser zoom would fire instead).
  useEffect(() => {
    const el = targetRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const next = clampScale(scaleRef.current + -e.deltaY * 0.0015);
      if (next === scaleRef.current) return;
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // ignore storage errors
      }
      setScale(next);
      window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [targetRef]);

  return scale;
}

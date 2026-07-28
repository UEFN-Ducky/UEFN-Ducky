import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "uefn-chat-column-width";
const CHANGE_EVENT = "uefn-chat-column-width-changed";
export const DEFAULT_CHAT_COLUMN_WIDTH = 960;
export const MIN_CHAT_COLUMN_WIDTH = 480;
export const MAX_CHAT_COLUMN_WIDTH = 960;

/** CSS custom property on `.chat-column-shell` — layout tracks shell size without React re-renders. */
export const CHAT_COLUMN_WIDTH_VAR = "--chat-column-width";

function clampPreference(width: number): number {
  return Math.min(MAX_CHAT_COLUMN_WIDTH, Math.max(MIN_CHAT_COLUMN_WIDTH, Math.round(width)));
}

export function readChatColumnWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_CHAT_COLUMN_WIDTH;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return DEFAULT_CHAT_COLUMN_WIDTH;
    return clampPreference(parsed);
  } catch {
    return DEFAULT_CHAT_COLUMN_WIDTH;
  }
}

export function persistChatColumnWidth(width: number): void {
  const next = clampPreference(width);
  try {
    localStorage.setItem(STORAGE_KEY, String(next));
  } catch {
    // ignore storage errors
  }
  window.dispatchEvent(new CustomEvent<number>(CHANGE_EVENT, { detail: next }));
}

function clampWidth(width: number, maxAvailable: number): number {
  const effectiveMin = Math.min(MIN_CHAT_COLUMN_WIDTH, maxAvailable);
  // Live width may exceed preference MAX when Ctrl+zoom scales it; shell is the ceiling.
  return Math.min(maxAvailable, Math.max(effectiveMin, Math.round(width)));
}

export function useChatColumnWidthSetting() {
  const [width, setWidthState] = useState(readChatColumnWidth);

  useEffect(() => {
    const sync = (event: Event) => {
      const next = event instanceof CustomEvent && typeof event.detail === "number"
        ? event.detail
        : readChatColumnWidth();
      setWidthState(clampPreference(next));
    };
    window.addEventListener(CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const setWidth = useCallback((next: number) => {
    persistChatColumnWidth(next);
    setWidthState(clampPreference(next));
  }, []);

  return { width, setWidth };
}

export function useChatColumnWidth() {
  const shellRef = useRef<HTMLDivElement>(null);
  const desiredWidthRef = useRef(readChatColumnWidth());
  const zoomRef = useRef(1);
  const liveWidthRef = useRef(desiredWidthRef.current);
  const { width: desiredWidth } = useChatColumnWidthSetting();

  desiredWidthRef.current = desiredWidth;

  const applyColumnWidth = useCallback((width: number) => {
    liveWidthRef.current = width;
    const shell = shellRef.current;
    if (shell) {
      shell.style.setProperty(CHAT_COLUMN_WIDTH_VAR, `${width}px`);
    }
  }, []);

  const reapply = useCallback(() => {
    const shell = shellRef.current;
    const maxAvailable = shell?.clientWidth || desiredWidthRef.current;
    const scaled = desiredWidthRef.current * zoomRef.current;
    applyColumnWidth(clampWidth(scaled, maxAvailable));
  }, [applyColumnWidth]);

  /** Ctrl+wheel: scale column with zoom. Zoom-out restores the settings base (e.g. 960). Does not persist. */
  const setZoomScale = useCallback((zoom: number) => {
    zoomRef.current = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
    reapply();
  }, [reapply]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;

    let raf = 0;
    const updateFromShell = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        reapply();
      });
    };

    updateFromShell();
    const observer = new ResizeObserver(updateFromShell);
    observer.observe(shell);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [reapply]);

  useEffect(() => {
    reapply();
  }, [desiredWidth, reapply]);

  return { shellRef, setZoomScale, columnWidth: liveWidthRef.current };
}

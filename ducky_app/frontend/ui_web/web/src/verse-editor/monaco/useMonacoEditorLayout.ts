import { useEffect, useState, type RefObject } from "react";
import type { editor } from "monaco-editor";

const RESIZE_SETTLE_MS = 150;

function safeLayout(ed: editor.IStandaloneCodeEditor | null): void {
  if (!ed) return;
  try {
    if (ed.getModel()?.isDisposed()) return;
    ed.layout();
  } catch {
    /* editor disposed during window resize / tab switch */
  }
}

/** Manual Monaco layout with rAF batching — avoids minimap decoration flicker from stacked layouts. */
export function useMonacoEditorLayout(
  containerRef: RefObject<HTMLElement | null>,
  editorRef: RefObject<editor.IStandaloneCodeEditor | null>,
  enabled: boolean,
): boolean {
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const container = containerRef.current;
    if (!container) return;

    let rafId = 0;
    let settleTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleLayout = () => {
      setIsResizing(true);
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        safeLayout(editorRef.current);
      });
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => {
        setIsResizing(false);
        safeLayout(editorRef.current);
      }, RESIZE_SETTLE_MS);
    };

    const ro = new ResizeObserver(scheduleLayout);
    ro.observe(container);
    window.addEventListener("resize", scheduleLayout);
    scheduleLayout();

    return () => {
      ro.disconnect();
      window.removeEventListener("resize", scheduleLayout);
      if (rafId) cancelAnimationFrame(rafId);
      if (settleTimer) clearTimeout(settleTimer);
    };
  }, [enabled, containerRef, editorRef]);

  return isResizing;
}

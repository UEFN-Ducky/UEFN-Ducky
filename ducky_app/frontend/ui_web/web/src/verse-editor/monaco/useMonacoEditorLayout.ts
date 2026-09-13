import { useEffect, useState, type RefObject } from "react";
import { frameBatch } from "../../utils/frameBatch";
import type { editor } from "monaco-editor";

const RESIZE_SETTLE_MS = 150;

function safeLayout(ed: editor.IStandaloneCodeEditor | null, width: number, height: number): void {
  if (!ed) return;
  try {
    if (ed.getModel()?.isDisposed()) return;
    ed.layout({ width, height });
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

    let lastWidth = 0;
    let lastHeight = 0;
    let settleTimer: ReturnType<typeof setTimeout> | undefined;

    const batch = frameBatch(() => {
      const width = container.clientWidth;
      const height = container.clientHeight;
      // Hidden panes must not collapse their editor to zero; showing them triggers RO.
      if (width <= 0 || height <= 0) {
        lastWidth = lastHeight = 0;
        return;
      }
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      setIsResizing(true);
      safeLayout(editorRef.current, width, height);
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => setIsResizing(false), RESIZE_SETTLE_MS);
    });

    const ro = new ResizeObserver(batch.schedule);
    ro.observe(container);
    batch.schedule();

    return () => {
      ro.disconnect();
      batch.cancel();
      if (settleTimer) clearTimeout(settleTimer);
    };
  }, [enabled, containerRef, editorRef]);

  return isResizing;
}

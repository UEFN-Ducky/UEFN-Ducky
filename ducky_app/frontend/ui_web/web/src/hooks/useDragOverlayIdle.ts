import { useCallback, useEffect, useMemo, useRef } from "react";

/** Dragover keeps arriving while a drag is real. After it stops, hide the box. */
export const DRAG_OVERLAY_IDLE_MS = 280;

/**
 * Hide the blue dashed drop box when a drag dies without dragend.
 * WebView2 often skips dragend (cancelled drag, or the drag started in
 * another window), which left the overlay stuck on the pane.
 */
export function useDragOverlayIdle(release: () => void): {
  bump: () => void;
  cancel: () => void;
} {
  const releaseRef = useRef(release);
  releaseRef.current = release;
  const timer = useRef(0);

  const cancel = useCallback(() => {
    window.clearTimeout(timer.current);
  }, []);

  const bump = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => releaseRef.current(), DRAG_OVERLAY_IDLE_MS);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return useMemo(() => ({ bump, cancel }), [bump, cancel]);
}

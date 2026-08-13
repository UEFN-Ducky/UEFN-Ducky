import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../hooks/usePanelApi";
import { isNativeWindowChrome, markNativeWindowChromeBody } from "../utils/nativeWindowChrome";
import {
  MAIN_MIN_HEIGHT,
  MAIN_MIN_WIDTH,
  SIDEBAR_ONLY_MIN_WIDTH,
} from "../constants/windowLayout";

type Edge = "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se";

const GRIPS: Edge[] = ["n", "s", "e", "w", "nw", "ne", "sw", "se"];

function applyResize(
  edge: Edge,
  dx: number,
  dy: number,
  bounds: { x: number; y: number; width: number; height: number },
  minW: number,
  minH: number,
) {
  let { x, y, width, height } = bounds;
  if (edge.includes("e")) width = Math.max(minW, width + dx);
  if (edge.includes("s")) height = Math.max(minH, height + dy);
  if (edge.includes("w")) {
    const nw = Math.max(minW, width - dx);
    x += width - nw;
    width = nw;
  }
  if (edge.includes("n")) {
    const nh = Math.max(minH, height - dy);
    y += height - nh;
    height = nh;
  }
  return { x, y, width, height };
}

interface WindowResizeProps {
  focusMode?: boolean;
  compactMode?: boolean;
}

/**
 * Edge/corner resize grips for NON-Windows platforms. On Windows the OS owns L/R/B
 * natively (see win_frameless.py); we only render a top grip. Other platforms drive
 * JS set_window_bounds via grips portalled to <body>.
 */
export function WindowResize({ focusMode = false, compactMode = false }: WindowResizeProps) {
  const [useNative, setUseNative] = useState(() => isNativeWindowChrome());
  const minW = focusMode || compactMode ? SIDEBAR_ONLY_MIN_WIDTH : MAIN_MIN_WIDTH;
  const minH = focusMode || compactMode ? SIDEBAR_ONLY_MIN_WIDTH : MAIN_MIN_HEIGHT;
  const pending = useRef<{ x: number; y: number; width: number; height: number } | null>(null);
  const rafId = useRef(0);

  useEffect(() => {
    const sync = () => {
      if (isNativeWindowChrome()) {
        markNativeWindowChromeBody();
        setUseNative(true);
      }
    };
    sync();
    window.addEventListener("pywebviewready", sync);
    return () => window.removeEventListener("pywebviewready", sync);
  }, []);

  const flush = () => {
    rafId.current = 0;
    const next = pending.current;
    pending.current = null;
    if (!next) return;
    const api = getApi();
    if (api) void api.set_window_bounds(next.x, next.y, next.width, next.height);
  };

  const schedule = (next: { x: number; y: number; width: number; height: number }) => {
    pending.current = next;
    if (!rafId.current) {
      rafId.current = requestAnimationFrame(flush);
    }
  };

  const onPointerDown = (edge: Edge) => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const api = getApi();
    if (!api) return;

    // Set up capture + listeners synchronously so no early motion is lost; the window
    // bounds resolve a moment later (local IPC) and gate the first applied resize.
    const startX = e.screenX;
    const startY = e.screenY;
    const target = e.currentTarget as HTMLElement;
    const pointerId = e.pointerId;
    try {
      target.setPointerCapture(pointerId);
    } catch {
      /* capture may be unavailable */
    }

    let bounds: { x: number; y: number; width: number; height: number } | null = null;
    let scale = 1;
    let active = true;

    const onMove = (ev: PointerEvent) => {
      if (!bounds) return;
      const dx = (ev.screenX - startX) / scale;
      const dy = (ev.screenY - startY) / scale;
      schedule(applyResize(edge, dx, dy, bounds, minW, minH));
    };

    const onUp = () => {
      active = false;
      if (rafId.current) {
        cancelAnimationFrame(rafId.current);
        rafId.current = 0;
      }
      flush();
      try {
        target.releasePointerCapture(pointerId);
      } catch {
        /* grip may already be gone */
      }
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    void api.get_window_bounds().then((b) => {
      if (!active) return;
      scale = b.scale && b.scale > 0 ? b.scale : 1;
      bounds = b;
    });
  };

  // Windows: OS frame handles L/R/B. Main window keeps a thin top grip (caption
  // stripped). Focus windows skip it — that strip sat on top of the hover/drag
  // band and stole press-and-hold as a resize instead of a move.
  const edges = useNative ? (focusMode ? [] : (["n"] as Edge[])) : GRIPS;
  if (edges.length === 0) return null;

  return createPortal(
    <>
      {edges.map((edge) => (
        <div
          key={edge}
          // Top-edge grips sit above the focus window's hover band, so they double as
          // the peek-header trigger (see .focus-top-edge-grip in global.css).
          className={`no-drag window-resize-grip window-resize-grip--${edge}${
            edge.startsWith("n") ? " focus-top-edge-grip" : ""
          }`}
          onPointerDown={onPointerDown(edge)}
        />
      ))}
    </>,
    document.body,
  );
}

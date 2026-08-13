import { useEffect } from "react";
import { getApi } from "../hooks/usePanelApi";
import { isNativeWindowChrome, markNativeWindowChromeBody } from "../utils/nativeWindowChrome";
import { beginNativeWindowMove, isWindowDragTarget } from "../utils/windowDrag";

/** Frameless drag: sync native move on mousedown (Aero Snap). JS fallback on other platforms. */
export function WindowDrag() {
  useEffect(() => {
    markNativeWindowChromeBody();
    // pywebview injects window.pywebview asynchronously; if it wasn't ready at mount,
    // re-mark once it is so the body class + native-chrome branch are correct.
    const onReady = () => markNativeWindowChromeBody();
    window.addEventListener("pywebviewready", onReady);

    let dragging = false;
    let pointerDown = false;
    let x0 = 0;
    let y0 = 0;
    let scale = 1;
    let origin = { x: 0, y: 0, width: 0, height: 0 };
    let rafId = 0;
    let pending: { x: number; y: number } | null = null;

    const flush = () => {
      rafId = 0;
      const next = pending;
      pending = null;
      if (!next || !dragging) return;
      const api = getApi();
      if (!api) return;
      void api.set_window_bounds(next.x, next.y, origin.width, origin.height);
    };

    const schedule = (x: number, y: number) => {
      pending = { x, y };
      if (!rafId) rafId = requestAnimationFrame(flush);
    };

    const onDoubleClick = (e: MouseEvent) => {
      if (!isWindowDragTarget(e.target)) return;
      // Native chrome: the OS maximises on double-click of the app-region caption.
      // Firing toggle_maximize too would double-toggle and could wedge the move-loop.
      // Evaluated live (not captured at mount) so a late pywebview injection is respected.
      if (isNativeWindowChrome()) return;
      const api = getApi();
      if (api) void api.toggle_maximize();
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      if (!isWindowDragTarget(e.target)) return;

      // Windows: WebView2 -webkit-app-region handles drag when the hit-tester is in
      // sync (those regions never reach JS). Focus overlay captions are often missed
      // (auto-hide header / max-height:0), so hand off to Win32 while LMB is down.
      if (isNativeWindowChrome()) {
        beginNativeWindowMove(e.screenX, e.screenY);
        return;
      }

      dragging = false;
      pointerDown = true;
      x0 = e.screenX;
      y0 = e.screenY;
      const api = getApi();
      if (!api) return;
      void api.get_window_bounds().then((bounds) => {
        // Guard the async race: if the button was already released before bounds
        // resolved, do NOT enter drag — otherwise the window sticks to the cursor
        // with no button held.
        if (!pointerDown) return;
        scale = bounds.scale && bounds.scale > 0 ? bounds.scale : 1;
        origin = bounds;
        dragging = true;
        document.body.classList.add("is-window-dragging");
      });
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = (e.screenX - x0) / scale;
      const dy = (e.screenY - y0) / scale;
      schedule(Math.round(origin.x + dx), Math.round(origin.y + dy));
    };

    const endDrag = () => {
      pointerDown = false;
      if (!dragging) return;
      dragging = false;
      if (rafId) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
      flush();
      document.body.classList.remove("is-window-dragging");
    };

    window.addEventListener("dblclick", onDoubleClick, true);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);

    return () => {
      window.removeEventListener("pywebviewready", onReady);
      window.removeEventListener("dblclick", onDoubleClick, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      endDrag();
    };
  }, []);

  return null;
}

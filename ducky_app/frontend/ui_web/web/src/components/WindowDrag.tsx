import { useEffect } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { isNativeWindowChrome, markNativeWindowChromeBody } from "../utils/nativeWindowChrome";
import { beginNativeWindowMove, isHeaderDragTarget, isWindowDragTarget, showNativeWindowMenu } from "../utils/windowDrag";

/** Frameless captions enter the native move loop on Windows, with a JS fallback elsewhere. */
export function WindowDrag() {
  useEffect(() => {
    // React can mount before pywebview is injected. An initially "remote"
    // result must not prevent the desktop handlers from starting later.
    let installed = false;
    let dragging = false;
    let pointerDown = false;
    let x0 = 0;
    let y0 = 0;
    let scale = 1;
    let origin = { x: 0, y: 0, width: 0, height: 0 };
    let rafId = 0;
    let pending: { x: number; y: number } | null = null;
    let nativeDragStart: { x: number; y: number } | null = null;

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
      // The header opts out of WebView2 caption regions, so this is its only
      // double-click handler. Other native drag surfaces still belong to the OS.
      if (isNativeWindowChrome() && !isHeaderDragTarget(e.target)) return;
      const api = getApi();
      if (api) void api.toggle_maximize();
    };

    const onContextMenu = (e: MouseEvent) => {
      if (!isNativeWindowChrome() || !isHeaderDragTarget(e.target)) return;
      if (showNativeWindowMenu()) e.preventDefault();
    };

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      if (!isWindowDragTarget(e.target)) return;

      // The header uses DOM input consistently. Other native caption surfaces
      // keep their existing fallback for events that WebView2 forwards to JS.
      if (isNativeWindowChrome()) {
        if (isHeaderDragTarget(e.target)) {
          // Let stationary clicks finish in the DOM so double-click works.
          // Once the pointer moves, Win32 owns the drag, restore and Aero Snap.
          nativeDragStart = { x: e.screenX, y: e.screenY };
        } else {
          beginNativeWindowMove(e.screenX, e.screenY);
        }
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
      if (nativeDragStart) {
        if (!(e.buttons & 1)) {
          nativeDragStart = null;
        } else if (Math.abs(e.screenX - nativeDragStart.x) >= 4 || Math.abs(e.screenY - nativeDragStart.y) >= 4) {
          nativeDragStart = null;
          beginNativeWindowMove(e.screenX, e.screenY);
        }
        return;
      }
      if (!dragging) return;
      const dx = (e.screenX - x0) / scale;
      const dy = (e.screenY - y0) / scale;
      schedule(Math.round(origin.x + dx), Math.round(origin.y + dy));
    };

    const endDrag = () => {
      nativeDragStart = null;
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

    const onReady = () => {
      if (isRemote()) return;
      markNativeWindowChromeBody();
      if (!installed) {
        window.addEventListener("dblclick", onDoubleClick, true);
        window.addEventListener("contextmenu", onContextMenu, true);
        window.addEventListener("pointerdown", onPointerDown, true);
        window.addEventListener("pointermove", onPointerMove);
        window.addEventListener("pointerup", endDrag);
        window.addEventListener("pointercancel", endDrag);
        window.addEventListener("blur", endDrag);
        installed = true;
      }
      // Only disable WebView2 caption input once its DOM replacement is attached.
      document.body.classList.toggle("native-caption-input-ready", isNativeWindowChrome());
    };
    window.addEventListener("pywebviewready", onReady);
    onReady();

    return () => {
      window.removeEventListener("pywebviewready", onReady);
      window.removeEventListener("dblclick", onDoubleClick, true);
      window.removeEventListener("contextmenu", onContextMenu, true);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      window.removeEventListener("blur", endDrag);
      document.body.classList.remove("native-caption-input-ready");
      endDrag();
    };
  }, []);

  return null;
}

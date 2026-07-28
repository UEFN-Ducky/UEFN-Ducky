import { useEffect } from "react";

/** Wire the VS Code navigation gestures to back/forward:
 *  - Mouse back/forward buttons (button 3 / 4)
 *  - Alt+Left / Alt+Right
 *
 * `back`/`forward` are expected to be stable no-ops when there's nowhere to go, so this
 * hook doesn't need to know the can-navigate flags. We also swallow the mouse buttons'
 * default so WebView2 doesn't run its own (meaningless, SPA) history navigation. */
export function useNavigationShortcuts(back: () => void, forward: () => void): void {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        back();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        forward();
      }
    };

    // Stop WebView2 from acting on the dedicated back/forward mouse buttons itself.
    const swallow = (e: MouseEvent) => {
      if (e.button === 3 || e.button === 4) e.preventDefault();
    };
    const onMouseUp = (e: MouseEvent) => {
      if (e.button === 3) {
        e.preventDefault();
        back();
      } else if (e.button === 4) {
        e.preventDefault();
        forward();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("mousedown", swallow, true);
    window.addEventListener("mouseup", onMouseUp, true);
    window.addEventListener("auxclick", swallow, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("mousedown", swallow, true);
      window.removeEventListener("mouseup", onMouseUp, true);
      window.removeEventListener("auxclick", swallow, true);
    };
  }, [back, forward]);
}

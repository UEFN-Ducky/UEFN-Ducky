const DRAG_ROOT =
  ".drag-region, .pywebview-drag-region, .focus-drag-bar, .focus-header-reveal-band";
const INTERACTIVE =
  "button, input, textarea, select, option, a, label, summary, [role='button'], [contenteditable='true']";

type PyWebViewMoveBridge = {
  _jsApiCallback?: (funcName: string, params: unknown, id: string) => void;
};

export function isWindowDragTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest(".dropdown-panel")) return false;
  if (target.closest(".no-drag")) return false;
  if (target.closest(INTERACTIVE)) return false;
  const inFocusShell = target.closest(".focus-shell");
  if (!inFocusShell && target.closest(".selectable-text")) return false;
  if (inFocusShell && target.closest(".verse-editor-host")) return false;
  return !!target.closest(DRAG_ROOT);
}

/** Header captions use DOM input on Windows, then enter the native move loop. */
export function isHeaderDragTarget(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(".app-header") && isWindowDragTarget(target);
}

export function showNativeWindowMenu(): boolean {
  const pw = (window as unknown as { pywebview?: PyWebViewMoveBridge }).pywebview;
  if (!pw?._jsApiCallback) return false;
  pw._jsApiCallback("uefnNativeWindowMenu", [], "window-menu");
  return true;
}

/** Hand off while the mouse button is held; bypass the async PanelApi worker. */
export function beginNativeWindowMove(screenX: number, screenY: number): boolean {
  const pw = (globalThis as unknown as { window?: { pywebview?: PyWebViewMoveBridge } }).window
    ?.pywebview;
  if (!pw?._jsApiCallback) return false;
  pw._jsApiCallback("uefnNativeWindowMove", [screenX, screenY], "move");
  return true;
}

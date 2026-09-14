/** Chat list skips windowing work while a rail is dragging or open/close is in flight. */
export const DOCK_LAYOUT_IDLE_EVENT = "uefn-dock-layout-idle";

export function dockLayoutBusy(): boolean {
  if (typeof document === "undefined") return false;
  return Boolean(document.querySelector(".dock-rail-shell.is-resizing, .dock-rail-shell.is-animating"));
}

export function notifyDockLayoutIdle(): void {
  window.dispatchEvent(new Event(DOCK_LAYOUT_IDLE_EVENT));
}

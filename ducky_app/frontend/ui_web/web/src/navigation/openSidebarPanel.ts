import type { SidebarPanelTab } from "../types/panel";

export const OPEN_SIDEBAR_PANEL_EVENT = "ducky:open-sidebar-panel";
export const OPEN_DOCK_SIDE_EVENT = "ducky:open-dock-side";

export function requestOpenSidebarPanel(panel: SidebarPanelTab): void {
  window.dispatchEvent(new CustomEvent(OPEN_SIDEBAR_PANEL_EVENT, { detail: { panel } }));
}

/** Open the left or right dock (a tour pointing at it; both start closed on a fresh install). */
export function requestOpenDockSide(side: "left" | "right"): void {
  window.dispatchEvent(new CustomEvent(OPEN_DOCK_SIDE_EVENT, { detail: { side } }));
}

import type { SidebarPanelTab } from "../types/panel";

export const OPEN_SIDEBAR_PANEL_EVENT = "ducky:open-sidebar-panel";

export function requestOpenSidebarPanel(panel: SidebarPanelTab): void {
  window.dispatchEvent(new CustomEvent(OPEN_SIDEBAR_PANEL_EVENT, { detail: { panel } }));
}

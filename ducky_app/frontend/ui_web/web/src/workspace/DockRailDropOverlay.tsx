import type { DockRailInsertZone } from "../utils/dockPanelDrag";

export function DockRailDropOverlay({ zone }: { zone: DockRailInsertZone | null }) {
  if (!zone) return null;
  return <div className={`dock-rail-drop-overlay dock-rail-drop-overlay--${zone}`} />;
}

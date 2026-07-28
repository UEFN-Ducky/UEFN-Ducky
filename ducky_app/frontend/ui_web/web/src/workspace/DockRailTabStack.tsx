import { type ReactNode } from "react";
import { SidebarPanelTabs } from "../components/sidebar/SidebarPanelTabs";
import { useWorkspaceDock } from "./WorkspaceDockContext";
import { dockPanelTabsInOrder } from "./dockPanelTabMeta";
import { insertIndexForTabDrop } from "./dockTabInsertIndex";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import type { DockPanelId, DockSide } from "./workspaceDockStorage";

export type DockPanelBusyMap = Partial<
  Record<DockPanelId, { busy?: boolean; busyTitle?: string }>
>;

export function DockRailTabStack({
  side,
  panelIds,
  activePanelId,
  busyByPanel,
  onDockDropZoneChange,
  onDockDragChange,
  children,
}: {
  side: DockSide;
  panelIds: DockPanelId[];
  activePanelId?: DockPanelId;
  busyByPanel?: DockPanelBusyMap;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
  children: ReactNode;
}) {
  const dock = useWorkspaceDock();
  const stack = dock.stackForSide(side);

  if (panelIds.length === 0) return null;

  const tabs = dockPanelTabsInOrder(panelIds, stack.order).map((tab) => {
    const busy = busyByPanel?.[tab.id];
    return busy ? { ...tab, busy: !!busy.busy, busyTitle: busy.busyTitle } : tab;
  });
  // Provided by DockRailFamilyStack so the tab indicator matches the visible
  // body; fall back to local derivation if used standalone.
  const activePanel =
    activePanelId ?? (panelIds.includes(stack.focusedPanel) ? stack.focusedPanel : panelIds[0]!);

  const selectTab = (id: DockPanelId) => {
    dock.setFocusedPanel(side, id);
  };

  return (
    <div className="dock-rail-tab-stack sidebar-inner">
      <SidebarPanelTabs
        tabs={tabs}
        activeTab={activePanel}
        onSelect={selectTab}
        dockSide={side}
        onMovePanel={(panelId, targetSide, _insertIndex, dropTarget) => {
          const insertIndex = insertIndexForTabDrop(dropTarget, targetSide, dock.snapshot);
          dock.movePanel(panelId, targetSide, insertIndex);
          dock.setFocusedPanel(targetSide, panelId);
        }}
        onSwapPanels={(a, b) => dock.swapPanels(side, a, b)}
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
      />
      <div className="dock-rail-tab-bodies">{children}</div>
    </div>
  );
}

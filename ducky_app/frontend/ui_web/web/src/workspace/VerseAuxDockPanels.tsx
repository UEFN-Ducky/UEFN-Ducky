import { useMemo, useRef, type ReactNode } from "react";
import { CtrlWheelZoomRoot } from "../components/CtrlWheelZoomRoot";
import { sidebarPanelZoomKey } from "../hooks/useCtrlWheelZoom";
import { SidebarSectionHeader } from "../components/sidebar/SidebarSectionHeader";
import { SidebarStackedPanels } from "../components/sidebar/SidebarStackedPanels";
import { useWorkspaceDock } from "./WorkspaceDockContext";
import { dockPanelTabMeta } from "./dockPanelTabMeta";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import { isAuxDockId, type DockPanelId, type DockSide } from "./workspaceDockStorage";

type VersePanelDef = {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  busy?: boolean;
  busyTitle?: string;
  onTearOffOutside?: (at: { screenX: number; screenY: number }) => void;
};

/** Outline/History/Tester plus plugin docks (Discord, Ollama, …). */
export type VerseFamilyId = DockPanelId;
export type VerseFamilyPanels = Record<string, VersePanelDef>;

export function VerseAuxDockPanels({
  side,
  panelIds,
  versePanels,
  onDockDropZoneChange,
  onDockDragChange,
  bodiesOnly = false,
  activePanelId,
}: {
  side: DockSide;
  panelIds: DockPanelId[];
  versePanels: VerseFamilyPanels;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
  bodiesOnly?: boolean;
  /** Side-wide active panel (tabs mode); a body shows only if it matches. */
  activePanelId?: DockPanelId;
}) {
  const dock = useWorkspaceDock();
  const stackRef = useRef<HTMLDivElement | null>(null);
  const verseIds = panelIds.filter(isAuxDockId);
  const stack = dock.stackForSide(side);

  const stackedPanels = useMemo((): VerseFamilyPanels => {
    const next: VerseFamilyPanels = {};
    for (const id of verseIds) {
      const def = versePanels[id];
      if (!def) continue;
      next[id] = {
        ...def,
        icon: def.icon ?? dockPanelTabMeta(id).icon,
      };
    }
    return next;
  }, [verseIds.join("|"), versePanels]);

  if (verseIds.length === 0) return null;

  const collapsedForStack = Object.fromEntries(verseIds.map((id) => [id, !!stack.collapsed[id]])) as Record<
    DockPanelId,
    boolean
  >;
  const orderForStack = stack.order.filter(isAuxDockId).filter((id) => verseIds.includes(id));
  for (const id of verseIds) {
    if (!orderForStack.includes(id)) orderForStack.push(id);
  }

  // Body visibility in tabs mode is a per-SIDE decision, not per-family: only
  // the side's active panel shows. When it is a chats/files tab, none of ours
  // match and every outline/history body stays hidden (no stray divided panel).
  const bodyHidden = (tab: VerseFamilyId) =>
    activePanelId === tab ? "" : "sidebar-panel-hidden";

  if (bodiesOnly) {
    return (
      <>
        {verseIds.map((id) => {
          const def = versePanels[id];
          if (!def) return null;
          return (
            <div
              key={id}
              className={`sidebar-panel-content ${bodyHidden(id)}`}
              data-dock-panel-id={id}
            >
              <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey(id)}>
                <SidebarSectionHeader
                  title={def.title}
                  busy={def.busy}
                  busyTitle={def.busyTitle}
                  actions={def.actions}
                />
                <div className="sidebar-panel-scroll">{def.children}</div>
              </CtrlWheelZoomRoot>
            </div>
          );
        })}
      </>
    );
  }

  if (verseIds.length === 1) {
    const only = verseIds[0]!;
    return (
      <SidebarStackedPanels<VerseFamilyId>
        stackRef={stackRef}
        order={[only]}
        splitRatio={stack.splitRatio}
        panelFlex={stack.panelFlex}
        collapsed={collapsedForStack}
        onToggleCollapsed={(id) => dock.toggleCollapsed(side, id)}
        onSwapPanels={(panelA, panelB, edge) => dock.swapPanels(side, panelA, panelB, edge)}
        onResizeSplit={(splitIndex, delta, h, snapshot) => dock.resizeSplit(side, splitIndex, delta, h, snapshot)}
        onPersistSplit={dock.persistSplit}
        onMovePanelToSide={(panelId, targetSide, insertIndex) => dock.movePanel(panelId, targetSide, insertIndex)}
        dockSide={side}
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
        panels={stackedPanels}
      />
    );
  }

  return (
    <SidebarStackedPanels<VerseFamilyId>
      stackRef={stackRef}
      order={orderForStack}
      splitRatio={stack.splitRatio}
      panelFlex={stack.panelFlex}
      collapsed={collapsedForStack}
      onToggleCollapsed={(id) => dock.toggleCollapsed(side, id)}
      onSwapPanels={(panelA, panelB, edge) => dock.swapPanels(side, panelA, panelB, edge)}
      onResizeSplit={(splitIndex, delta, h, snapshot) => dock.resizeSplit(side, splitIndex, delta, h, snapshot)}
      onPersistSplit={dock.persistSplit}
      onMovePanelToSide={(panelId, targetSide, insertIndex) => dock.movePanel(panelId, targetSide, insertIndex)}
      dockSide={side}
      onDockDropZoneChange={onDockDropZoneChange}
      onDockDragChange={onDockDragChange}
      panels={stackedPanels}
    />
  );
}

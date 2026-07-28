import { useMemo, useRef, type ReactNode } from "react";
import { Icons } from "../icons/Icons";
import { CtrlWheelZoomRoot } from "../components/CtrlWheelZoomRoot";
import { sidebarPanelZoomKey } from "../hooks/useCtrlWheelZoom";
import { SidebarSectionHeader } from "../components/sidebar/SidebarSectionHeader";
import { SidebarStackedPanels } from "../components/sidebar/SidebarStackedPanels";
import { useWorkspaceDock } from "./WorkspaceDockContext";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import type { DockPanelId, DockSide } from "./workspaceDockStorage";

type VersePanelDef = {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  busy?: boolean;
  busyTitle?: string;
  onTearOffOutside?: (at: { screenX: number; screenY: number }) => void;
};

/** Panels rendered by the "verse aux" family: Outline/History/Tester plus Discord Ducky + hub. */
export type VerseFamilyId = "outline" | "history" | "tester" | "groupchat" | "discordhub";
export type VerseFamilyPanels = Record<VerseFamilyId, VersePanelDef>;

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

  const isVerseFamily = (id: DockPanelId): id is VerseFamilyId =>
    id === "outline" ||
    id === "history" ||
    id === "tester" ||
    id === "groupchat" ||
    id === "discordhub";
  const verseIds = panelIds.filter(isVerseFamily);
  if (verseIds.length === 0) return null;

  const stack = dock.stackForSide(side);

  const stackedPanels = useMemo((): VerseFamilyPanels => {
    return {
      outline: {
        ...versePanels.outline,
        icon: <Icons.Outline />,
      },
      history: {
        ...versePanels.history,
        icon: <Icons.Clock />,
      },
      tester: {
        ...versePanels.tester,
        icon: <Icons.Check />,
      },
      groupchat: {
        ...versePanels.groupchat,
        icon: <Icons.Chat />,
      },
      discordhub: {
        ...versePanels.discordhub,
        icon: <Icons.Chat />,
      },
    };
  }, [versePanels]);

  const collapsedForStack = Object.fromEntries(verseIds.map((id) => [id, stack.collapsed[id]])) as Record<
    DockPanelId,
    boolean
  >;
  const orderForStack = stack.order.filter(isVerseFamily).filter((id) => verseIds.includes(id));
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
        {verseIds.includes("outline") ? (
          <div
            className={`sidebar-panel-content ${bodyHidden("outline")}`}
            data-dock-panel-id="outline"
          >
            <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey("outline")}>
              <SidebarSectionHeader title="Outline" actions={versePanels.outline.actions} />
              <div className="sidebar-panel-scroll">{versePanels.outline.children}</div>
            </CtrlWheelZoomRoot>
          </div>
        ) : null}
        {verseIds.includes("history") ? (
          <div
            className={`sidebar-panel-content ${bodyHidden("history")}`}
            data-dock-panel-id="history"
          >
            <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey("history")}>
              <SidebarSectionHeader title="History" actions={versePanels.history.actions} />
              <div className="sidebar-panel-scroll">{versePanels.history.children}</div>
            </CtrlWheelZoomRoot>
          </div>
        ) : null}
        {verseIds.includes("tester") ? (
          <div
            className={`sidebar-panel-content ${bodyHidden("tester")}`}
            data-dock-panel-id="tester"
          >
            <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey("tester")}>
              <SidebarSectionHeader
                title="Tester"
                busy={versePanels.tester.busy}
                busyTitle={versePanels.tester.busyTitle}
                actions={versePanels.tester.actions}
              />
              <div className="sidebar-panel-scroll">{versePanels.tester.children}</div>
            </CtrlWheelZoomRoot>
          </div>
        ) : null}
        {verseIds.includes("groupchat") ? (
          <div
            className={`sidebar-panel-content ${bodyHidden("groupchat")}`}
            data-dock-panel-id="groupchat"
          >
            <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey("groupchat")}>
              <SidebarSectionHeader title="Discord Ducky" actions={versePanels.groupchat.actions} />
              <div className="sidebar-panel-scroll">{versePanels.groupchat.children}</div>
            </CtrlWheelZoomRoot>
          </div>
        ) : null}
        {verseIds.includes("discordhub") ? (
          <div
            className={`sidebar-panel-content ${bodyHidden("discordhub")}`}
            data-dock-panel-id="discordhub"
          >
            <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey("discordhub")}>
              <SidebarSectionHeader title="Discord" actions={versePanels.discordhub.actions} />
              <div className="sidebar-panel-scroll">{versePanels.discordhub.children}</div>
            </CtrlWheelZoomRoot>
          </div>
        ) : null}
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
        onSwapPanels={(panelA, panelB) => dock.swapPanels(side, panelA, panelB)}
        onResizeSplit={(splitIndex, delta, h) => dock.resizeSplit(side, splitIndex, delta, h)}
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
      onSwapPanels={(panelA, panelB) => dock.swapPanels(side, panelA, panelB)}
      onResizeSplit={(splitIndex, delta, h) => dock.resizeSplit(side, splitIndex, delta, h)}
      onPersistSplit={dock.persistSplit}
      onMovePanelToSide={(panelId, targetSide, insertIndex) => dock.movePanel(panelId, targetSide, insertIndex)}
      dockSide={side}
      onDockDropZoneChange={onDockDropZoneChange}
      onDockDragChange={onDockDragChange}
      panels={stackedPanels}
    />
  );
}

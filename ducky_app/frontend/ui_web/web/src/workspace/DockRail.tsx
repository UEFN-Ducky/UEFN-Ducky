import { useCallback, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { SplitResizeHandle } from "../components/SplitResizeHandle";

import { SidebarStackedPanels } from "../components/sidebar/SidebarStackedPanels";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

import { useWorkspaceDock } from "./WorkspaceDockContext";

import type { DockPanelId, DockSide } from "./workspaceDockStorage";

import type { DockDropTarget } from "../utils/dockPanelDrag";

import { DockRailDropOverlay } from "./DockRailDropOverlay";

import {
  OUTLINE_PANEL_WIDTH_MIN,
} from "../hooks/useOutlinePanelWidth";
import { SIDEBAR_WIDTH_MIN } from "../hooks/useSidebarWidth";
import { targetRef } from "../ui-targets/registry";



const HANDLE_WIDTH = 1;

const PEEK_WIDTH = 200;



type PanelDef = {

  title: string;

  icon?: ReactNode;

  actions?: ReactNode;

  onContextMenu?: (e: React.MouseEvent) => void;

  searchMode?: boolean;

  children: ReactNode;

};



export function DockRail({

  side,

  open,

  peek,

  resizeDisabled,

  panelIds,

  panels,

  children,

  dragOverlay,

  onDockDropZoneChange,

  onDockDragChange,

}: {

  side: DockSide;

  open: boolean;

  peek?: boolean;

  resizeDisabled?: boolean;

  panelIds?: DockPanelId[];

  panels?: Partial<Record<DockPanelId, PanelDef>>;

  children?: ReactNode;

  dragOverlay?: DockDropTarget | null;

  onDockDropZoneChange?: (target: DockDropTarget) => void;

  onDockDragChange?: (dragging: boolean) => void;

}) {

  const dock = useWorkspaceDock();

  const scopeClass = useScopedClass(`dock-rail-${side}`);

  const stackRef = useRef<HTMLDivElement | null>(null);

  const [isResizing, setIsResizing] = useState(false);



  const ids = panelIds ?? [];

  const hasPanels = ids.length > 0 || children != null;

  const isPeek = !!peek && !hasPanels;

  const width = side === "left" ? dock.leftWidth : dock.rightWidth;

  const stack = dock.stackForSide(side);

  const railWidth = isPeek ? PEEK_WIDTH : width;

  const shellWidth = (open || isPeek) && (hasPanels || isPeek) ? railWidth + HANDLE_WIDTH : 0;

  const railOpen = open || isPeek;



  const panelStyle = useMemo(

    (): CSSProperties =>

      ({

        "--dock-rail-width": `${railWidth}px`,

      }) as CSSProperties,

    [railWidth],

  );



  const onResize = useCallback(

    (delta: number) => {

      setIsResizing(true);

      const next =

        side === "left"

          ? Math.min(560, Math.max(SIDEBAR_WIDTH_MIN, width + delta))

          : Math.min(480, Math.max(OUTLINE_PANEL_WIDTH_MIN, width - delta));

      dock.resizeRailWidth(side, next);

    },

    [dock, side, width],

  );



  const handleResizeEnd = useCallback(() => {

    setIsResizing(false);

    dock.persistRailWidth();

  }, [dock]);



  if (!hasPanels && !isPeek) return null;



  const collapsedForStack = Object.fromEntries(

    ids.map((id) => [id, stack.collapsed[id]]),

  ) as Record<DockPanelId, boolean>;



  const orderForStack = stack.order.filter((id) => ids.includes(id));

  for (const id of ids) {

    if (!orderForStack.includes(id)) orderForStack.push(id);

  }



  const stackedPanels = panels ?? {};

  const showDropOverlay = dragOverlay?.side === side;



  return (

    <div

      ref={targetRef(side === "left" ? "shell.left" : "shell.right", {
        kind: "button",
        label: side === "left" ? "Left side" : "Right side",
        route: "chat",
      })}

      className={`dock-rail-shell dock-rail-shell--${side} ${scopeClass} ${railOpen ? "is-open" : ""}${isPeek ? " is-peek" : ""}${isResizing ? " is-resizing" : ""}`}

      style={panelStyle}

    >

      <ScopedCss

        selector={`.${scopeClass}`}

        rules={{

          "--dock-shell-width": `${shellWidth}px`,

          "--dock-rail-width": `${railWidth}px`,

        }}

      />

      {side === "right" && railOpen && !resizeDisabled && !isPeek ? (

        <SplitResizeHandle

          className="dock-rail-resize-handle"

          onDrag={onResize}

          onDragEnd={handleResizeEnd}

          ariaLabel={`Resize ${side} panel`}

        />

      ) : null}

      <aside

        className={`dock-rail dock-rail--${side}${railOpen ? " is-open" : ""}${isPeek ? " is-peek" : ""}`}

        aria-hidden={!railOpen}

        data-dock-side={side}

      >

        {showDropOverlay ? (

          <DockRailDropOverlay zone={dragOverlay?.insert ?? "center"} />

        ) : null}

        <div className="dock-rail-inner sidebar-inner">

          {isPeek ? (

            <div className="dock-rail-peek-placeholder" aria-hidden="true">

              Drop panel here

            </div>

          ) : null}

          {children ??

            (ids.length > 0 ? (

              <SidebarStackedPanels

                stackRef={stackRef}

                order={orderForStack}

                splitRatio={stack.splitRatio}
                panelFlex={stack.panelFlex}

                collapsed={collapsedForStack}

                onToggleCollapsed={(id) => dock.toggleCollapsed(side, id)}

                onSwapPanels={(panelA, panelB) => dock.swapPanels(side, panelA, panelB)}

                onResizeSplit={(splitIndex, delta, h) => dock.resizeSplit(side, splitIndex, delta, h)}

                onPersistSplit={dock.persistSplit}

                onMovePanelToSide={(panelId, targetSide, insertIndex) =>

                  dock.movePanel(panelId, targetSide, insertIndex)

                }

                dockSide={side}

                onDockDropZoneChange={onDockDropZoneChange}

                onDockDragChange={onDockDragChange}

                panels={stackedPanels as Record<DockPanelId, PanelDef>}

              />

            ) : null)}

        </div>

      </aside>

      {side === "left" && railOpen && !resizeDisabled && !isPeek ? (

        <SplitResizeHandle

          className="dock-rail-resize-handle"

          onDrag={onResize}

          onDragEnd={handleResizeEnd}

          ariaLabel={`Resize ${side} panel`}

        />

      ) : null}

    </div>

  );

}


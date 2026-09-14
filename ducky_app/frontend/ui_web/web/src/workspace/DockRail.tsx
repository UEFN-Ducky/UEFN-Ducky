import { useCallback, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { SplitResizeHandle } from "../components/SplitResizeHandle";

import { SidebarStackedPanels } from "../components/sidebar/SidebarStackedPanels";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

import { useWorkspaceDock } from "./WorkspaceDockContext";

import type { DockPanelId, DockSide } from "./workspaceDockStorage";

import type { DockDropTarget } from "../utils/dockPanelDrag";

import { DockRailDropOverlay } from "./DockRailDropOverlay";

import {
  OUTLINE_PANEL_WIDTH_MAX,
  OUTLINE_PANEL_WIDTH_MIN,
} from "../hooks/useOutlinePanelWidth";
import { SIDEBAR_WIDTH_MAX, SIDEBAR_WIDTH_MIN } from "../hooks/useSidebarWidth";
import { notifyDockLayoutIdle } from "./dockLayoutEvents";
import { railResizeEnd, railResizeFrame } from "./dockRailResize";
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

  overlay,

  swipeTx,

  resizeDisabled,

  panelIds,

  panels,

  children,

  dragOverlay,

  onDockDropZoneChange,

  onDockDragChange,

  onClose,

}: {

  side: DockSide;

  open: boolean;

  peek?: boolean;

  overlay?: boolean;

  swipeTx?: number | null;

  resizeDisabled?: boolean;

  panelIds?: DockPanelId[];

  panels?: Partial<Record<DockPanelId, PanelDef>>;

  children?: ReactNode;

  dragOverlay?: DockDropTarget | null;

  onDockDropZoneChange?: (target: DockDropTarget) => void;

  onDockDragChange?: (dragging: boolean) => void;

  onClose?: () => void;

}) {

  const dock = useWorkspaceDock();

  const scopeClass = useScopedClass(`dock-rail-${side}`);

  const stackRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const widthRef = useRef(0);
  const railOpenRef = useRef<boolean | null>(null);
  const [isResizing, setIsResizing] = useState(false);



  const ids = panelIds ?? [];

  const hasPanels = ids.length > 0 || children != null;

  const isPeek = !!peek && !hasPanels;

  const width = side === "left" ? dock.leftWidth : dock.rightWidth;

  const stack = dock.stackForSide(side);

  const railWidth = isPeek ? PEEK_WIDTH : width;

  const railOpen = open || isPeek;
  if (!isResizing) widthRef.current = railWidth;
  const visualWidth = isResizing ? widthRef.current : railWidth;
  const shellWidth = (overlay || railOpen) && (hasPanels || isPeek) ? visualWidth + HANDLE_WIDTH : 0;
  const resizeRailWidth = dock.resizeRailWidth;
  const persistRailWidth = dock.persistRailWidth;

  const panelStyle = useMemo(

    (): CSSProperties =>

      ({

        "--dock-rail-width": `${visualWidth}px`,

      }) as CSSProperties,

    [visualWidth],

  );

  const minWidth = side === "left" ? SIDEBAR_WIDTH_MIN : OUTLINE_PANEL_WIDTH_MIN;
  const maxWidth = side === "left" ? SIDEBAR_WIDTH_MAX : OUTLINE_PANEL_WIDTH_MAX;

  const onResize = useCallback(
    (delta: number) => {
      setIsResizing(true);
      const el = shellRef.current;
      el?.classList.add("is-resizing");
      const next = railResizeFrame(side, widthRef.current, delta, maxWidth);
      widthRef.current = next;
      if (el) {
        el.style.setProperty("--dock-rail-width", `${next}px`);
        el.style.setProperty("--dock-shell-width", `${next + HANDLE_WIDTH}px`);
      }
      if (next >= minWidth) resizeRailWidth(side, next);
    },
    [maxWidth, minWidth, resizeRailWidth, side],
  );

  const handleResizeEnd = useCallback(() => {
    const released = railResizeEnd(widthRef.current, minWidth, maxWidth);
    if (released.close) {
      persistRailWidth();
      onClose?.();
    } else {
      widthRef.current = released.width;
      resizeRailWidth(side, released.width);
      persistRailWidth();
    }
    notifyDockLayoutIdle();
    shellRef.current?.classList.remove("is-resizing");
    setIsResizing(false);
  }, [maxWidth, minWidth, onClose, persistRailWidth, resizeRailWidth, side]);

  useLayoutEffect(() => {
    if (railOpenRef.current === null) {
      railOpenRef.current = railOpen;
      return;
    }
    if (railOpenRef.current === railOpen) return;
    railOpenRef.current = railOpen;
    const el = shellRef.current;
    if (!el) return;
    el.classList.add("is-animating");
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      el.classList.remove("is-animating");
      window.clearTimeout(timeout);
      el.removeEventListener("transitionend", onEnd);
      notifyDockLayoutIdle();
    };
    const onEnd = (event: TransitionEvent) => {
      if (event.target !== el || event.propertyName !== "width") return;
      finish();
    };
    el.addEventListener("transitionend", onEnd);
    const timeout = window.setTimeout(finish, 400);
    return () => {
      window.clearTimeout(timeout);
      el.removeEventListener("transitionend", onEnd);
    };
  }, [railOpen]);

  const assignShellRef = useCallback(
    (el: HTMLDivElement | null) => {
      shellRef.current = el;
      targetRef(side === "left" ? "shell.left" : "shell.right", {
        kind: "button",
        label: side === "left" ? "Left side" : "Right side",
        route: "chat",
      })(el);
    },
    [side],
  );

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

      ref={assignShellRef}

      className={`dock-rail-shell dock-rail-shell--${side} ${scopeClass} ${railOpen ? "is-open" : ""}${overlay ? " is-overlay" : ""}${swipeTx != null ? " is-swiping" : ""}${isPeek ? " is-peek" : ""}${isResizing ? " is-resizing" : ""}`}

      style={
        swipeTx != null
          ? ({ ...panelStyle, "--dock-swipe-tx": `${swipeTx}px` } as CSSProperties)
          : panelStyle
      }

    >

      <ScopedCss

        selector={`.${scopeClass}`}

        rules={{

          "--dock-shell-width": `${shellWidth}px`,

          "--dock-rail-width": `${visualWidth}px`,

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

        aria-hidden={!railOpen && swipeTx == null}

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


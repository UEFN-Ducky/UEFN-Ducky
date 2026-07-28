import { useCallback, useRef, useState, type CSSProperties, type MutableRefObject, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { SplitResizeHandle } from "../SplitResizeHandle";
import { CtrlWheelZoomRoot } from "../CtrlWheelZoomRoot";
import { sidebarPanelZoomKey } from "../../hooks/useCtrlWheelZoom";
import { SidebarSectionHeader } from "./SidebarSectionHeader";
import {
  classifyDockDropTarget,
  dockDropInsertIndex,
  type DockDropTarget,
} from "../../utils/dockPanelDrag";
import { classifySidebarDragOut } from "../../utils/sidebarDragOut";
import {
  stackedPanelDropHint,
  type StackedPanelDropHint,
} from "../../utils/stackedPanelDropHint";
import { flexGrowForStackedPanel } from "../../utils/stackedPanelFlex";
import type { DockSide } from "../../workspace/workspaceDockStorage";

const DRAG_THRESHOLD_PX = 5;
const HORIZONTAL_DRAG_THRESHOLD_PX = 12;

function isBlockingOverlayOpen(): boolean {
  return !!document.querySelector(".modal-backdrop, .quick-open-overlay");
}

export function SidebarStackedPanel<TId extends string>({
  panelId,
  title,
  icon,
  busy = false,
  busyTitle,
  collapsed,
  flexGrow,
  actions,
  onContextMenu,
  onToggleCollapse,
  onSwapPanels,
  dropHint,
  onDropHintChange,
  stackRef,
  onPanelRef,
  isDragging,
  onDragStateChange,
  onDockDropZoneChange,
  dockSide,
  onMovePanelToSide,
  onTearOffOutside,
  searchMode,
  children,
}: {
  panelId: TId;
  title: string;
  icon?: ReactNode;
  busy?: boolean;
  busyTitle?: string;
  collapsed: boolean;
  flexGrow: number;
  actions?: ReactNode;
  onContextMenu?: (e: React.MouseEvent) => void;
  onToggleCollapse: () => void;
  onSwapPanels: (panelA: TId, panelB: TId) => void;
  dropHint: StackedPanelDropHint<TId> | null;
  onDropHintChange?: (hint: StackedPanelDropHint<TId> | null) => void;
  stackRef: MutableRefObject<HTMLDivElement | null>;
  onPanelRef?: (el: HTMLDivElement | null) => void;
  isDragging: boolean;
  onDragStateChange: (panelId: TId | null) => void;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  dockSide?: DockSide;
  onMovePanelToSide?: (panelId: TId, targetSide: DockSide, insertIndex?: number) => void;
  /** Drag clearly outside the OS window → tear off (e.g. Discord → focus window). */
  onTearOffOutside?: (at: { screenX: number; screenY: number }) => void;
  searchMode?: boolean;
  children: ReactNode;
}) {
  const dragStartY = useRef(0);
  const dragStartX = useRef(0);
  const dragMovedRef = useRef(false);
  const pendingDropTargetRef = useRef<DockDropTarget>(null);
  const pendingDropHintRef = useRef<StackedPanelDropHint<TId> | null>(null);
  const lastPointerRef = useRef({ clientX: 0, clientY: 0, screenX: 0, screenY: 0 });
  const panelElRef = useRef<HTMLDivElement | null>(null);
  const [ghostPos, setGhostPos] = useState<{ x: number; y: number } | null>(null);

  const onHeaderPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (isBlockingOverlayOpen()) return;
      // Collapse / drag are left-click only — right-click must open the context menu.
      if (e.button !== 0) return;
      if (
        (e.target as HTMLElement).closest(".sidebar-section-header-actions, button, a, input")
      )
        return;
      e.preventDefault();
      const headerEl = e.currentTarget;
      headerEl.setPointerCapture(e.pointerId);
      dragStartY.current = e.clientY;
      dragStartX.current = e.clientX;
      dragMovedRef.current = false;
      pendingDropTargetRef.current = null;
      pendingDropHintRef.current = null;
      lastPointerRef.current = {
        clientX: e.clientX,
        clientY: e.clientY,
        screenX: e.screenX,
        screenY: e.screenY,
      };
      setGhostPos(null);
      onDragStateChange(panelId);

      const onPointerMove = (ev: PointerEvent) => {
        lastPointerRef.current = {
          clientX: ev.clientX,
          clientY: ev.clientY,
          screenX: ev.screenX,
          screenY: ev.screenY,
        };
        const movedY = Math.abs(ev.clientY - dragStartY.current) > DRAG_THRESHOLD_PX;
        const movedX = Math.abs(ev.clientX - dragStartX.current) > HORIZONTAL_DRAG_THRESHOLD_PX;
        if (movedY || movedX) {
          dragMovedRef.current = true;
          setGhostPos({ x: ev.clientX, y: ev.clientY });
        }

        if (!dragMovedRef.current) return;

        pendingDropTargetRef.current = null;
        pendingDropHintRef.current = null;

        if (onMovePanelToSide && dockSide) {
          const dropTarget = classifyDockDropTarget(ev.clientX, ev.clientY);
          onDockDropZoneChange?.(dropTarget);
          if (dropTarget && dropTarget.side !== dockSide) {
            pendingDropTargetRef.current = dropTarget;
            onDropHintChange?.(null);
            return;
          }
        }

        onDockDropZoneChange?.(null);

        const stack = stackRef.current;
        if (!stack) {
          onDropHintChange?.(null);
          return;
        }

        const panelEls = stack.querySelectorAll<HTMLElement>(".sidebar-stacked-panel");
        if (panelEls.length < 2) {
          onDropHintChange?.(null);
          return;
        }

        const hitRects = Array.from(panelEls).flatMap((el) => {
          const id = el.dataset.panelId as TId | undefined;
          if (!id) return [];
          const rect = el.getBoundingClientRect();
          return [
            {
              id,
              top: rect.top,
              bottom: rect.bottom,
              collapsed: el.classList.contains("is-collapsed"),
            },
          ];
        });

        const hint = stackedPanelDropHint(hitRects, panelId, ev.clientY);
        pendingDropHintRef.current = hint;
        onDropHintChange?.(hint);
      };

      const endDrag = () => {
        try {
          headerEl.releasePointerCapture(e.pointerId);
        } catch {
          // ignore if capture was already released
        }

        const dropTarget = pendingDropTargetRef.current;
        const dropHintPending = pendingDropHintRef.current;
        const didDrag = dragMovedRef.current;
        const pointer = lastPointerRef.current;

        onDragStateChange(null);
        onDockDropZoneChange?.(null);
        onDropHintChange?.(null);
        setGhostPos(null);
        pendingDropTargetRef.current = null;
        pendingDropHintRef.current = null;
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", endDrag);
        window.removeEventListener("pointercancel", endDrag);

        if (dropTarget && onMovePanelToSide && dockSide) {
          const targetRail = document.querySelector<HTMLElement>(`.dock-rail--${dropTarget.side}`);
          const panelCount =
            targetRail?.querySelectorAll<HTMLElement>(".sidebar-stacked-panel").length ?? 0;
          const insertIndex = dockDropInsertIndex(dropTarget, panelCount);
          onMovePanelToSide(panelId, dropTarget.side, insertIndex);
        } else if (dropHintPending) {
          onSwapPanels(panelId, dropHintPending.targetId);
        } else if (didDrag && onTearOffOutside) {
          const zone = classifySidebarDragOut(pointer);
          if (zone?.kind === "outside") {
            onTearOffOutside({ screenX: zone.screenX, screenY: zone.screenY });
          }
        } else if (!didDrag && !isBlockingOverlayOpen()) {
          onToggleCollapse();
        }

        window.setTimeout(() => {
          dragMovedRef.current = false;
        }, 0);
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", endDrag);
      window.addEventListener("pointercancel", endDrag);
    },
    [
      dockSide,
      onDockDropZoneChange,
      onDragStateChange,
      onMovePanelToSide,
      onSwapPanels,
      onDropHintChange,
      onTearOffOutside,
      onToggleCollapse,
      panelId,
      stackRef,
    ],
  );

  const style: CSSProperties = { flex: `${collapsed ? 0 : flexGrow} 1 0%` };

  const setPanelRef = useCallback(
    (el: HTMLDivElement | null) => {
      panelElRef.current = el;
      onPanelRef?.(el);
    },
    [onPanelRef],
  );

  const hintEdge = dropHint?.targetId === panelId ? dropHint.edge : null;

  return (
    <>
      <div
        ref={setPanelRef}
        className={`sidebar-stacked-panel${collapsed ? " is-collapsed" : ""}${isDragging ? " is-dragging" : ""}${hintEdge ? ` is-drop-${hintEdge}` : ""}${searchMode ? " sidebar-stacked-panel--search" : ""}`}
        style={style}
        data-panel-id={panelId}
      >
        <SidebarSectionHeader
          title={title}
          icon={icon}
          busy={busy}
          busyTitle={busyTitle}
          actions={actions}
          onContextMenu={onContextMenu}
          draggable
          headerClassName={`sidebar-section-header--stacked${isDragging ? " is-dragging" : ""}`}
          onHeaderPointerDown={onHeaderPointerDown}
        />
        <div className="sidebar-stacked-panel-body" aria-hidden={collapsed || undefined}>
          <CtrlWheelZoomRoot className="sidebar-body" storageKey={sidebarPanelZoomKey(panelId)}>
            {children}
          </CtrlWheelZoomRoot>
        </div>
      </div>
      {ghostPos && isDragging
        ? createPortal(
            <div
              className="dock-panel-drag-ghost sidebar-drag-overlay"
              style={{ left: ghostPos.x, top: ghostPos.y }}
            >
              <span className="dock-panel-drag-ghost-title">{title}</span>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export function SidebarStackedPanels<TId extends string>({
  order,
  splitRatio,
  panelFlex,
  collapsed,
  onToggleCollapsed,
  onSwapPanels,
  onResizeSplit,
  onPersistSplit,
  onMovePanelToSide,
  dockSide,
  onDockDropZoneChange,
  onDockDragChange,
  stackRef,
  panels,
  minPanelHeight = 80,
}: {
  order: TId[];
  splitRatio: number;
  panelFlex?: Partial<Record<TId, number>>;
  collapsed: Record<TId, boolean>;
  onToggleCollapsed: (id: TId) => void;
  onSwapPanels: (panelA: TId, panelB: TId) => void;
  onResizeSplit: (splitIndex: number, deltaPx: number, containerHeight: number) => void;
  onPersistSplit: () => void;
  onMovePanelToSide?: (panelId: TId, targetSide: DockSide, insertIndex?: number) => void;
  dockSide?: DockSide;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
  stackRef: MutableRefObject<HTMLDivElement | null>;
  minPanelHeight?: number;
  panels: Record<
    TId,
    {
      title: string;
      icon?: ReactNode;
      busy?: boolean;
      busyTitle?: string;
      actions?: ReactNode;
      onContextMenu?: (e: React.MouseEvent) => void;
      searchMode?: boolean;
      onTearOffOutside?: (at: { screenX: number; screenY: number }) => void;
      children: ReactNode;
    }
  >;
}) {
  const [draggingPanelId, setDraggingPanelId] = useState<TId | null>(null);
  const [dropHint, setDropHint] = useState<StackedPanelDropHint<TId> | null>(null);

  const setDraggingId = useCallback(
    (id: TId | null) => {
      setDraggingPanelId(id);
      onDockDragChange?.(id !== null);
      if (!id) setDropHint(null);
    },
    [onDockDragChange],
  );

  const handleSwapPanels = useCallback(
    (panelA: TId, panelB: TId) => {
      onSwapPanels(panelA, panelB);
    },
    [onSwapPanels],
  );

  const openCount = order.reduce((n, id) => n + (collapsed[id] ? 0 : 1), 0);

  const flexInput = { order, collapsed, panelFlex, splitRatio, minPanelHeight };

  const flexFor = (id: TId): number => {
    if (openCount === 0) return 0;
    if (openCount === 1 && !collapsed[id]) return 1;
    return flexGrowForStackedPanel(flexInput, id);
  };

  const showSplitAfter = (id: TId, index: number): boolean => {
    if (index >= order.length - 1) return false;
    const nextId = order[index + 1]!;
    return !collapsed[id] && !collapsed[nextId];
  };

  return (
    <div
      ref={(el) => {
        if (stackRef) stackRef.current = el;
      }}
      className="sidebar-stacked-panels"
    >
      {order.map((id, index) => {
        const panel = panels[id];
        if (!panel) return null;
        const nodes: ReactNode[] = [
          <SidebarStackedPanel
            key={id}
            panelId={id}
            title={panel.title}
            icon={panel.icon}
            busy={panel.busy}
            busyTitle={panel.busyTitle}
            collapsed={collapsed[id]}
            flexGrow={flexFor(id)}
            actions={panel.actions}
            onContextMenu={panel.onContextMenu}
            onToggleCollapse={() => onToggleCollapsed(id)}
            onSwapPanels={handleSwapPanels as (panelA: string, panelB: string) => void}
            dropHint={dropHint}
            onDropHintChange={setDropHint as (hint: StackedPanelDropHint<string> | null) => void}
            stackRef={stackRef}
            isDragging={draggingPanelId === id}
            onDragStateChange={setDraggingId as (panelId: string | null) => void}
            onDockDropZoneChange={onDockDropZoneChange}
            dockSide={dockSide}
            onMovePanelToSide={onMovePanelToSide as ((panelId: string, targetSide: DockSide, insertIndex?: number) => void) | undefined}
            onTearOffOutside={panel.onTearOffOutside}
            searchMode={panel.searchMode}
          >
            {panel.children}
          </SidebarStackedPanel>,
        ];

        if (showSplitAfter(id, index)) {
          nodes.push(
            <SplitResizeHandle
              key={`split-${id}`}
              className="sidebar-panel-split"
              orientation="vertical"
              ariaLabel="Resize sidebar panels"
              onDrag={(delta) => {
                const h = stackRef.current?.clientHeight ?? 0;
                onResizeSplit(index, delta, h);
              }}
              onDragEnd={onPersistSplit}
            />,
          );
        }

        return nodes;
      })}
    </div>
  );
}

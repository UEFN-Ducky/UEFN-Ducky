import {
  useCallback,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { classifyDockDropTarget, type DockDropTarget } from "../../utils/dockPanelDrag";
import type { DockSide } from "../../workspace/workspaceDockStorage";

const DRAG_THRESHOLD_PX = 5;
const HORIZONTAL_DRAG_THRESHOLD_PX = 12;

export type SidebarPanelTabItem<T extends string> = {
  id: T;
  title: string;
  icon: ReactNode;
  /** Activity inside this panel (visible even when another tab is selected). */
  busy?: boolean;
  busyTitle?: string;
};

function isBlockingOverlayOpen(): boolean {
  return !!document.querySelector(".modal-backdrop, .quick-open-overlay");
}

function SidebarTabButton({
  tabId,
  active,
  title,
  icon,
  busy = false,
  busyTitle = "Working…",
  isDragging,
  isSwapHint,
  onPointerDown,
}: {
  tabId: string;
  active: boolean;
  title: string;
  icon: ReactNode;
  busy?: boolean;
  busyTitle?: string;
  isDragging?: boolean;
  isSwapHint?: boolean;
  onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => void;
}) {
  const label = busy ? `${title} — ${busyTitle}` : title;
  return (
    <button
      type="button"
      role="tab"
      className={`sidebar-panel-tab${active ? " is-active" : ""}${isDragging ? " is-dragging" : ""}${isSwapHint ? " is-swap-hint" : ""}${busy ? " is-busy" : ""}`}
      title={label}
      aria-label={label}
      aria-selected={active}
      aria-busy={busy || undefined}
      data-panel-tab-id={tabId}
      onPointerDown={onPointerDown}
    >
      {icon}
      {busy ? (
        <span className="sidebar-agent-spinner sidebar-panel-tab-busy" aria-hidden="true" />
      ) : null}
    </button>
  );
}

export function SidebarPanelTabs<T extends string>({
  tabs,
  activeTab,
  onSelect,
  dockSide,
  onMovePanel,
  onSwapPanels,
  onDockDropZoneChange,
  onDockDragChange,
}: {
  tabs: SidebarPanelTabItem<T>[];
  activeTab: T;
  onSelect: (id: T) => void;
  dockSide?: DockSide;
  onMovePanel?: (
    panelId: T,
    targetSide: DockSide,
    insertIndex: number | undefined,
    dropTarget: DockDropTarget,
  ) => void;
  onSwapPanels?: (panelA: T, panelB: T) => void;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
}) {
  const tabBarRef = useRef<HTMLDivElement>(null);
  const [draggingTabId, setDraggingTabId] = useState<T | null>(null);
  const [swapHintWith, setSwapHintWith] = useState<T | null>(null);
  const [ghost, setGhost] = useState<{ x: number; y: number; title: string } | null>(null);

  const activeIndex = Math.max(0, tabs.findIndex((tab) => tab.id === activeTab));

  const onTabPointerDown = useCallback(
    (tab: SidebarPanelTabItem<T>) => (e: React.PointerEvent<HTMLButtonElement>) => {
      if (isBlockingOverlayOpen()) return;
      e.preventDefault();
      const button = e.currentTarget;
      button.setPointerCapture(e.pointerId);
      const startX = e.clientX;
      const startY = e.clientY;
      let moved = false;
      let pendingDropTarget: DockDropTarget = null;
      let pendingSwapWith: T | null = null;

      setDraggingTabId(tab.id);
      setGhost(null);
      setSwapHintWith(null);
      onDockDragChange?.(true);

      const onPointerMove = (ev: PointerEvent) => {
        const movedX = Math.abs(ev.clientX - startX) > HORIZONTAL_DRAG_THRESHOLD_PX;
        const movedY = Math.abs(ev.clientY - startY) > DRAG_THRESHOLD_PX;
        if (movedX || movedY) {
          moved = true;
          setGhost({ x: ev.clientX, y: ev.clientY, title: tab.title });
        }
        if (!moved) return;

        pendingDropTarget = null;
        pendingSwapWith = null;
        setSwapHintWith(null);

        if (onMovePanel && dockSide) {
          const dropTarget = classifyDockDropTarget(ev.clientX, ev.clientY);
          onDockDropZoneChange?.(dropTarget);
          if (dropTarget && dropTarget.side !== dockSide) {
            pendingDropTarget = dropTarget;
            return;
          }
        }
        onDockDropZoneChange?.(null);

        if (onSwapPanels && tabBarRef.current) {
          const tabEls = tabBarRef.current.querySelectorAll<HTMLElement>("[data-panel-tab-id]");
          for (const tabEl of tabEls) {
            const otherId = tabEl.dataset.panelTabId as T | undefined;
            if (!otherId || otherId === tab.id) continue;
            const rect = tabEl.getBoundingClientRect();
            if (
              ev.clientX >= rect.left &&
              ev.clientX <= rect.right &&
              ev.clientY >= rect.top &&
              ev.clientY <= rect.bottom
            ) {
              pendingSwapWith = otherId;
              setSwapHintWith(otherId);
              return;
            }
          }
        }
      };

      const endDrag = () => {
        try {
          button.releasePointerCapture(e.pointerId);
        } catch {
          // ignore if capture was already released
        }

        const dropTarget = pendingDropTarget;
        const swapWith = pendingSwapWith;
        const didDrag = moved;

        setDraggingTabId(null);
        setSwapHintWith(null);
        setGhost(null);
        onDockDropZoneChange?.(null);
        onDockDragChange?.(false);
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", endDrag);
        window.removeEventListener("pointercancel", endDrag);

        if (dropTarget && onMovePanel && dockSide) {
          onMovePanel(tab.id, dropTarget.side, undefined, dropTarget);
        } else if (swapWith && onSwapPanels) {
          onSwapPanels(tab.id, swapWith);
        } else if (!didDrag && !isBlockingOverlayOpen()) {
          onSelect(tab.id);
        }
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", endDrag);
      window.addEventListener("pointercancel", endDrag);
    },
    [dockSide, onDockDragChange, onDockDropZoneChange, onMovePanel, onSelect, onSwapPanels],
  );

  if (tabs.length === 0) return null;

  const tabStyle = {
    "--sidebar-tab-count": tabs.length,
    "--sidebar-tab-index": activeIndex,
  } as CSSProperties;

  return (
    <>
      <div className="sidebar-header">
        <div
          ref={tabBarRef}
          className="sidebar-panel-tabs"
          role="tablist"
          style={tabStyle}
        >
          <span className="sidebar-panel-tab-indicator" aria-hidden="true" />
          {tabs.map((tab) => (
            <SidebarTabButton
              key={tab.id}
              tabId={tab.id}
              active={activeTab === tab.id}
              title={tab.title}
              icon={tab.icon}
              busy={tab.busy}
              busyTitle={tab.busyTitle}
              isDragging={draggingTabId === tab.id}
              isSwapHint={swapHintWith === tab.id}
              onPointerDown={onTabPointerDown(tab)}
            />
          ))}
        </div>
      </div>
      {ghost
        ? createPortal(
            <div
              className="dock-panel-drag-ghost sidebar-drag-overlay"
              style={{ left: ghost.x, top: ghost.y }}
            >
              <span className="dock-panel-drag-ghost-title">{ghost.title}</span>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

/** Hit-test dock rails during cross-side panel header drag. */

import type { DockSide } from "../workspace/workspaceDockStorage";

export type DockRailInsertZone = "top" | "bottom" | "center";

export type DockDropTarget = {
  side: DockSide;
  insert: DockRailInsertZone;
} | null;

/** @deprecated Use DockDropTarget — kept for gradual migration */
export type DockDropZone = DockSide | null;

const EDGE_PEEK_PX = 96;

export function dockDropTargetSide(target: DockDropTarget): DockSide | null {
  return target?.side ?? null;
}

export function dockDropInsertIndex(
  target: DockDropTarget,
  panelCount: number,
): number | undefined {
  if (!target) return undefined;
  if (target.insert === "top") return 0;
  if (target.insert === "bottom") return panelCount;
  if (panelCount === 0) return 0;
  return panelCount;
}

export function dropZoneFromPointer(
  rect: DOMRect,
  _clientX: number,
  clientY: number,
  edgeRatio = 0.25,
): DockRailInsertZone {
  const relY = (clientY - rect.top) / rect.height;
  if (relY < edgeRatio) return "top";
  if (relY > 1 - edgeRatio) return "bottom";
  return "center";
}

function railHit(side: DockSide): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `.dock-rail--${side}.is-open, .dock-rail--${side}.is-peek`,
  );
}

function classifyRailTarget(
  side: DockSide,
  clientX: number,
  clientY: number,
): DockDropTarget | null {
  const rail = railHit(side);
  if (!rail) return null;
  const rect = rail.getBoundingClientRect();
  if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
    return null;
  }
  return { side, insert: dropZoneFromPointer(rect, clientX, clientY) };
}

export function classifyDockDropTarget(clientX: number, clientY: number): DockDropTarget {
  const center = document.querySelector<HTMLElement>(".workspace-dock-center");
  if (center) {
    const rect = center.getBoundingClientRect();
    if (clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom) {
      return null;
    }
  }

  const left = classifyRailTarget("left", clientX, clientY);
  if (left) return left;
  const right = classifyRailTarget("right", clientX, clientY);
  if (right) return right;

  if (clientX < EDGE_PEEK_PX) return { side: "left", insert: "center" };
  if (clientX > window.innerWidth - EDGE_PEEK_PX) return { side: "right", insert: "center" };
  return null;
}

/** @deprecated Use classifyDockDropTarget */
export function classifyDockDropZone(clientX: number, clientY: number): DockDropZone {
  return dockDropTargetSide(classifyDockDropTarget(clientX, clientY));
}

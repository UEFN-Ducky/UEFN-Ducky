/** Same-rail stacked panel reorder: which edge gets the glowing drop line. */

export type StackedPanelDropEdge = "before" | "after";

export type StackedPanelDropHint<TId extends string = string> = {
  targetId: TId;
  edge: StackedPanelDropEdge;
};

export type StackedPanelHitRect<TId extends string = string> = {
  id: TId;
  top: number;
  bottom: number;
  /** Collapsed / header-only — whole band is a drop target, not just past midpoint. */
  collapsed: boolean;
};

const GAP_SLOP_PX = 6;

/**
 * Pick the panel under the pointer and which edge should show the insert line.
 * Collapsed rows count the full height so Duckies/Outline/History reorder without expanding.
 */
export function stackedPanelDropHint<TId extends string>(
  panels: StackedPanelHitRect<TId>[],
  draggedId: TId,
  pointerY: number,
): StackedPanelDropHint<TId> | null {
  const dragged = panels.find((p) => p.id === draggedId);
  if (!dragged) return null;

  let best: { panel: StackedPanelHitRect<TId>; dist: number } | null = null;

  for (const panel of panels) {
    if (panel.id === draggedId) continue;
    const top = panel.top - GAP_SLOP_PX;
    const bottom = panel.bottom + GAP_SLOP_PX;
    if (pointerY < top || pointerY > bottom) continue;

    const mid = (panel.top + panel.bottom) / 2;
    const dist = Math.abs(pointerY - mid);
    if (!best || dist < best.dist) best = { panel, dist };
  }

  if (!best) return null;

  const { panel } = best;
  const draggedIsAbove = dragged.top < panel.top;
  const height = Math.max(1, panel.bottom - panel.top);
  const pastMid =
    (draggedIsAbove && pointerY > panel.top + height / 2) ||
    (!draggedIsAbove && pointerY < panel.top + height / 2);

  // Collapsed headers are ~28px — require any hover in the band, not midpoint.
  if (!panel.collapsed && !pastMid) return null;

  return {
    targetId: panel.id,
    edge: draggedIsAbove ? "before" : "after",
  };
}

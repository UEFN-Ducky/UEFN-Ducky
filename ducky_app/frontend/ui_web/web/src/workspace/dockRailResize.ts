import type { DockSide } from "./workspaceDockStorage";

/** Release below this (px) collapses the rail instead of clamping to min width. */
export const RAIL_COLLAPSE_WIDTH = 80;

export function railResizeFrame(side: DockSide, current: number, delta: number, max: number): number {
  const raw = side === "left" ? current + delta : current - delta;
  return Math.min(max, Math.max(0, raw));
}

export function railResizeEnd(
  visual: number,
  min: number,
  max: number,
  collapseAt = RAIL_COLLAPSE_WIDTH,
): { close: true } | { close: false; width: number } {
  if (visual < collapseAt) return { close: true };
  return { close: false, width: Math.min(max, Math.max(min, visual)) };
}

import type { DockSide } from "./workspaceDockStorage";

/** Release below this (px) collapses the rail instead of clamping to min width. */
export const RAIL_COLLAPSE_WIDTH = 80;

export function railResizeFrame(side: DockSide, current: number, delta: number, max: number): number {
  const raw = side === "left" ? current + delta : current - delta;
  return Math.min(max, Math.max(0, raw));
}

function clampRailWidth(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Persist `width` (never 0). `close` means hide the rail; reopen uses this width. */
export function railResizeEnd(
  visual: number,
  min: number,
  max: number,
  startWidth = min,
  collapseAt = RAIL_COLLAPSE_WIDTH,
): { close: boolean; width: number } {
  if (visual < collapseAt) return { close: true, width: clampRailWidth(startWidth, min, max) };
  return { close: false, width: clampRailWidth(visual, min, max) };
}

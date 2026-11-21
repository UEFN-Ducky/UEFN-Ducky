import type { AutomationGraphNodeDto } from "../types/panel";

/** Every card is the same size; settings live in the side panel, not on the card. */
export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 82;
/** Zoomed far out a card is only its name over its big icon: thinner, but tall enough to read. */
export const NODE_HEIGHT_COMPACT = 72;
/** The coloured title bar at the top of a card. */
export const NODE_TITLE_HEIGHT = 34;
/** Ports sit on the first row under the title bar, like Unreal's exec pins. */
export const PORT_Y = 52;
/** Below this zoom cards turn thin (only their name and icon). */
export const OVERVIEW_ZOOM = 0.5;
export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 4;

export function clampZoom(zoom: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

export function portPoint(node: AutomationGraphNodeDto, direction: "in" | "out", compact = false) {
  return { x: node.x + (direction === "out" ? NODE_WIDTH : 0), y: node.y + (compact ? NODE_HEIGHT_COMPACT / 2 : PORT_Y) };
}

/** Group titles shrink with the canvas when zoomed out, but never below ~10px on screen. */
export function groupTitleScale(zoom: number): number {
  return Math.max(1, 0.625 / zoom);
}

/** The grid steps up by 8 when zoomed far out, so its lines never crowd together. */
export function gridScale(zoom: number): number {
  let scale = zoom;
  while (scale * 128 < 24) scale *= 8;
  return scale;
}

/** A wire that leaves to the right and enters from the left, bending more the further back it goes. */
export function wirePath(x1: number, y1: number, x2: number, y2: number): string {
  const c = Math.max(40, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + c} ${y1}, ${x2 - c} ${y2}, ${x2} ${y2}`;
}

export function zoomAt(pan: { x: number; y: number }, zoom: number, next: number, point: { x: number; y: number }) {
  return { x: point.x - (point.x - pan.x) * next / zoom, y: point.y - (point.y - pan.y) * next / zoom };
}

/** The camera that puts `box` (canvas units) in the middle of `view` (screen px, relative to the board).
 *  `maxZoom` keeps a single small node from filling the screen. */
export function fitCamera(box: { x: number; y: number; width: number; height: number }, view: { x: number; y: number; width: number; height: number }, maxZoom: number) {
  const zoom = clampZoom(Math.min(maxZoom, view.width / Math.max(1, box.width), view.height / Math.max(1, box.height)));
  return {
    zoom,
    x: view.x + (view.width - box.width * zoom) / 2 - box.x * zoom,
    y: view.y + (view.height - box.height * zoom) / 2 - box.y * zoom,
  };
}

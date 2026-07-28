/** Sidebar trees drag with dnd-kit (pointer events), so dragging past the
 * sidebar never produces native dragover/drop on the editor panes or other OS
 * windows. The trees track the pointer during a drag and call this on drag end
 * to decide whether the item left the sidebar — and if so, where it landed. */

import type { EditorDropZone } from "../types/panel";
import { dropZoneFromPointer } from "./editorLayoutOps";

export type SidebarDragPoint = {
  clientX: number;
  clientY: number;
  screenX: number;
  screenY: number;
};

export type SidebarEditorDrop = {
  groupId: string;
  zone: EditorDropZone;
};

export type SidebarDragOutZone =
  | ({ kind: "editor" } & SidebarEditorDrop)
  | { kind: "outside"; screenX: number; screenY: number };

/** Margin past a dock rail edge that still counts as the rail, so drops on the
 * resize handle keep normal sort semantics. */
const SIDEBAR_EDGE_SLACK = 4;

/** A drop must land at least this far beyond the window edge to tear off into a
 * floating window. The editor tab strip sits flush against the top of the
 * window, so a drop there often registers a hair past the top/side border; the
 * old strict `> innerWidth`/`< 0` test read that as "outside" and tore the file
 * off instead of opening it. Near-edge drops now stay inside and open in the
 * editor (VS Code behavior); tearing off requires a deliberate drop clearly
 * outside the window. */
const TEAR_OFF_MARGIN = 32;

/** Live preview while a sidebar item is dragged over the editor (split squares). */
let editorDropPreview: SidebarEditorDrop | null = null;
const previewListeners = new Set<() => void>();

export function getSidebarEditorDropPreview(): SidebarEditorDrop | null {
  return editorDropPreview;
}

export function setSidebarEditorDropPreview(next: SidebarEditorDrop | null): void {
  const same =
    editorDropPreview?.groupId === next?.groupId && editorDropPreview?.zone === next?.zone;
  if (same) return;
  editorDropPreview = next;
  for (const listener of previewListeners) listener();
}

export function subscribeSidebarEditorDropPreview(listener: () => void): () => void {
  previewListeners.add(listener);
  return () => {
    previewListeners.delete(listener);
  };
}

/** Whether the drop point is clearly outside the app window (a real tear-off).
 * Screen coords vs the window's on-screen rect are the reliable signal in
 * WebView2 — client coords can drift while a frameless window is being moved —
 * with a client-coord fallback when screen coords aren't reported. */
function isClearlyOutsideWindow(p: SidebarDragPoint): boolean {
  if (p.screenX !== 0 || p.screenY !== 0) {
    return (
      p.screenX < window.screenX - TEAR_OFF_MARGIN ||
      p.screenY < window.screenY - TEAR_OFF_MARGIN ||
      p.screenX > window.screenX + window.outerWidth + TEAR_OFF_MARGIN ||
      p.screenY > window.screenY + window.outerHeight + TEAR_OFF_MARGIN
    );
  }
  return (
    p.clientX < -TEAR_OFF_MARGIN ||
    p.clientY < -TEAR_OFF_MARGIN ||
    p.clientX > window.innerWidth + TEAR_OFF_MARGIN ||
    p.clientY > window.innerHeight + TEAR_OFF_MARGIN
  );
}

function railAt(side: "left" | "right"): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `.dock-rail--${side}.is-open, .dock-rail--${side}.is-peek`,
  );
}

function pointInRect(
  p: SidebarDragPoint,
  rect: { left: number; right: number; top: number; bottom: number },
  slack = 0,
): boolean {
  return (
    p.clientX >= rect.left - slack &&
    p.clientX <= rect.right + slack &&
    p.clientY >= rect.top - slack &&
    p.clientY <= rect.bottom + slack
  );
}

/** True when the pointer is still over a left/right dock rail (reorder, not open). */
export function isOverDockRail(p: SidebarDragPoint): boolean {
  for (const side of ["left", "right"] as const) {
    const rail = railAt(side);
    if (!rail) continue;
    if (pointInRect(p, rail.getBoundingClientRect(), SIDEBAR_EDGE_SLACK)) return true;
  }
  const legacy = document.querySelector<HTMLElement>(".sidebar-root.is-open, .sidebar-root");
  if (legacy && !railAt("left") && !railAt("right")) {
    return pointInRect(p, legacy.getBoundingClientRect(), SIDEBAR_EDGE_SLACK);
  }
  return false;
}

function editorGroupIdOf(el: Element | null | undefined): string {
  if (!el || !("dataset" in el)) return "";
  const id = (el as { dataset?: { editorGroupId?: string } }).dataset?.editorGroupId;
  return typeof id === "string" ? id : "";
}

/** Resolve which editor group + VS Code-style zone is under the pointer.
 * Uses geometry (not elementsFromPoint) so dnd-kit DragOverlay ghosts don't steal hits. */
export function resolveEditorDropAt(clientX: number, clientY: number): SidebarEditorDrop | null {
  const groups = document.querySelectorAll<HTMLElement>("[data-editor-group-id]");
  let best: HTMLElement | null = null;
  let bestArea = Number.POSITIVE_INFINITY;
  for (const el of groups) {
    const groupId = editorGroupIdOf(el);
    if (!groupId) continue;
    const r = el.getBoundingClientRect();
    if (clientX < r.left || clientX > r.right || clientY < r.top || clientY > r.bottom) continue;
    const area = Math.max(1, r.width * r.height);
    if (area < bestArea) {
      best = el;
      bestArea = area;
    }
  }
  if (!best) return null;
  return {
    groupId: editorGroupIdOf(best),
    zone: dropZoneFromPointer(best.getBoundingClientRect(), clientX, clientY),
  };
}

function fallbackEditorDrop(p: SidebarDragPoint): SidebarEditorDrop | null {
  const group = document.querySelector<HTMLElement>("[data-editor-group-id]");
  const groupId = editorGroupIdOf(group);
  if (!group || !groupId) return null;
  const rect = group.getBoundingClientRect();
  // Clamp into the pane so near-chrome drops (tab strip) still resolve a zone.
  const x = Math.min(Math.max(p.clientX, rect.left + 1), Math.max(rect.left + 1, rect.right - 1));
  const y = Math.min(Math.max(p.clientY, rect.top + 1), Math.max(rect.top + 1, rect.bottom - 1));
  return { groupId, zone: dropZoneFromPointer(rect, x, y) };
}

export function classifySidebarDragOut(p: SidebarDragPoint): SidebarDragOutZone | null {
  if (p.clientX === 0 && p.clientY === 0 && p.screenX === 0 && p.screenY === 0) return null;

  // Only a drop clearly past the window edge tears off into a floating window;
  // everything else lands inside and routes to the sidebar/editor branches below.
  if (isClearlyOutsideWindow(p)) {
    return {
      kind: "outside",
      screenX: p.screenX || window.screenX + p.clientX,
      screenY: p.screenY || window.screenY + p.clientY,
    };
  }

  // Still over left OR right dock → keep tree reorder / nest semantics.
  if (isOverDockRail(p)) return null;

  const drop = resolveEditorDropAt(p.clientX, p.clientY) ?? fallbackEditorDrop(p);
  if (drop) return { kind: "editor", ...drop };
  return null;
}

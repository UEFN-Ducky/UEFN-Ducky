import { useCallback, useEffect, useMemo, useRef, type DragEvent as ReactDragEvent } from "react";
import { beginTreeDrag, endTreeDrag, TREE_DRAG_MIME } from "../utils/editorTabDrag";
import {
  classifySidebarDragOut,
  editorDropForTreeDrag,
  setSidebarEditorDropPreview,
  type SidebarDragPoint,
  type SidebarEditorDrop,
} from "../utils/sidebarDragOut";
import {
  indicatorFor,
  isBranch,
  resolveDropTarget,
  topLevelSources,
  zoneForPointer,
  type DropPolicy,
  type DropTarget,
  type TreeModel,
} from "./treeMove";
import { findTreeElement, TreeDropDecor, TREE_NODE_ATTR, TREE_ROW_ATTR } from "./treeDropDecor";

export { TREE_NODE_ATTR, TREE_ROW_ATTR } from "./treeDropDecor";

/** Leaving the tree: only offered when the drop does something real. */
export interface TreeDropOutside {
  accepts: (sources: readonly string[]) => boolean;
  /** Dropped on an editor pane (VS Code-style split zone). */
  onEditor?: (sources: readonly string[], at: SidebarEditorDrop) => void;
  /** Let go clearly outside the window. */
  onTearOff?: (sources: readonly string[], at: { screenX: number; screenY: number }) => void;
}

export interface TreeDrop {
  sources: string[];
  target: DropTarget;
  model: TreeModel;
}

export interface TreeDndOptions {
  /** The tree as it is now (keep it memoized; it is read on every pointer move). */
  getModel: () => TreeModel;
  getPolicy: () => DropPolicy;
  /** Ids that travel together when `id` is picked up (the multi-selection). */
  dragSources: (id: string) => string[];
  canDrag?: (id: string) => boolean;
  onDrop: (drop: TreeDrop) => void;
  /** Value stored under the tree drag type (what kind of thing moves). */
  dragData?: (sources: readonly string[]) => string;
  /** Name shown in the floating preview. */
  labelFor?: (id: string) => string;
  dropOutside?: TreeDropOutside;
  /** Open a collapsed branch the pointer rests on. */
  expand?: (id: string) => void;
}

/** Hovering a closed folder this long while dragging opens it (VS Code). */
const EXPAND_DELAY_MS = 500;
/** Auto-scroll band at the top and bottom of the scrolling panel. */
const SCROLL_EDGE_PX = 32;
const SCROLL_MAX_STEP = 14;

type Session = {
  root: HTMLElement;
  sources: string[];
  model: TreeModel;
  target: DropTarget | null;
  decor: TreeDropDecor;
  sourceEls: Element[];
  expandId: string | null;
  expandTimer: number;
  scrollEl: HTMLElement | null;
  scrollRaf: number;
  point: SidebarDragPoint;
  editor: SidebarEditorDrop | null;
  preview: HTMLElement | null;
  detach: () => void;
};

function scrollParent(el: Element | null): HTMLElement | null {
  let current = el instanceof HTMLElement ? el : el?.parentElement ?? null;
  while (current) {
    const style = window.getComputedStyle(current);
    if (/(auto|scroll|overlay)/.test(style.overflowY) && current.scrollHeight > current.clientHeight) return current;
    current = current.parentElement;
  }
  return null;
}

/** The visible row nearest the pointer when it is between rows (null below the last). */
function nearestRow(root: HTMLElement, clientY: number): HTMLElement | null {
  let best: HTMLElement | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  let lastBottom = Number.NEGATIVE_INFINITY;
  for (const row of root.querySelectorAll<HTMLElement>(`[${TREE_ROW_ATTR}]`)) {
    const rect = row.getBoundingClientRect();
    if (!(rect.height > 0)) continue;
    if (clientY >= rect.top && clientY <= rect.bottom) return row;
    lastBottom = Math.max(lastBottom, rect.bottom);
    const distance = clientY < rect.top ? rect.top - clientY : clientY - rect.bottom;
    if (distance < bestDistance) {
      best = row;
      bestDistance = distance;
    }
  }
  // Blank space under the last row means "the end of the tree".
  if (clientY > lastBottom) return null;
  return best;
}

function pointOf(e: { clientX: number; clientY: number; screenX: number; screenY: number }): SidebarDragPoint | null {
  // WebView2 reports 0,0 once the pointer leaves the window; keep the last real spot.
  if (!e.clientX && !e.clientY && !e.screenX && !e.screenY) return null;
  return { clientX: e.clientX || 0, clientY: e.clientY || 0, screenX: e.screenX || 0, screenY: e.screenY || 0 };
}

function stripIds(el: Element): void {
  for (const node of [el, ...el.querySelectorAll("*")]) {
    for (const name of [...node.getAttributeNames()]) {
      if (name === "id" || name.startsWith("data-")) node.removeAttribute(name);
    }
  }
}

/** A light floating copy of the row (icon + name, with a count for several). */
function buildPreview(row: HTMLElement, label: string, count: number): HTMLElement {
  const box = document.createElement("div");
  box.className = "tree-drag-preview";
  const icon = row.querySelector(".sidebar-tree-row-icon, .aw-folder-icon, .aw-trigger, svg");
  if (icon) {
    const holder = document.createElement("span");
    holder.className = "tree-drag-preview-icon";
    const copy = icon.cloneNode(true) as Element;
    stripIds(copy);
    holder.appendChild(copy);
    box.appendChild(holder);
  }
  const text = document.createElement("span");
  text.className = "tree-drag-preview-label";
  text.textContent = label;
  box.appendChild(text);
  if (count > 1) {
    const badge = document.createElement("span");
    badge.className = "tree-drag-preview-count";
    badge.textContent = String(count);
    box.appendChild(badge);
  }
  document.body.appendChild(box);
  return box;
}

/**
 * One drag-and-drop engine for every sidebar tree: native HTML5 drag (OS-drawn
 * preview, Escape cancels, no hold delay), event delegation on the tree root, a crisp
 * insertion line or folder highlight, auto-scroll at the panel edges, and an editor
 * drop only when the pointer is really over an editor pane.
 */
export function useTreeDnd(options: TreeDndOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const rootRef = useRef<HTMLElement | null>(null);
  const sessionRef = useRef<Session | null>(null);

  const finish = useCallback((session: Session) => {
    if (sessionRef.current === session) sessionRef.current = null;
    session.decor.clear();
    for (const el of session.sourceEls) el.classList.remove("tree-drag-source");
    if (session.expandTimer) window.clearTimeout(session.expandTimer);
    if (session.scrollRaf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(session.scrollRaf);
    session.root.classList.remove("is-tree-dragging");
    document.body.classList.remove("tree-dragging");
    session.preview?.remove();
    session.detach();
    setSidebarEditorDropPreview(null);
    endTreeDrag();
  }, []);

  const track = useCallback((session: Session, e: { clientX: number; clientY: number; screenX: number; screenY: number }) => {
    const point = pointOf(e);
    if (point) session.point = point;
  }, []);

  const scheduleExpand = useCallback((session: Session, id: string | null) => {
    if (session.expandId === id) return;
    if (session.expandTimer) window.clearTimeout(session.expandTimer);
    session.expandTimer = 0;
    session.expandId = id;
    const expand = optionsRef.current.expand;
    if (!id || !expand) return;
    session.expandTimer = window.setTimeout(() => {
      session.expandTimer = 0;
      if (sessionRef.current === session) expand(id);
    }, EXPAND_DELAY_MS);
  }, []);

  const startAutoScroll = useCallback((session: Session) => {
    if (!session.scrollEl || typeof requestAnimationFrame !== "function") return;
    const tick = () => {
      if (sessionRef.current !== session || !session.scrollEl) return;
      const rect = session.scrollEl.getBoundingClientRect();
      const { clientX: x, clientY: y } = session.point;
      if (rect.height > 0 && x >= rect.left && x <= rect.right) {
        const edge = Math.min(SCROLL_EDGE_PX, rect.height / 4);
        if (y < rect.top + edge && y >= rect.top - edge) {
          session.scrollEl.scrollTop -= Math.ceil(((rect.top + edge - y) / (2 * edge)) * SCROLL_MAX_STEP);
        } else if (y > rect.bottom - edge && y <= rect.bottom + edge) {
          session.scrollEl.scrollTop += Math.ceil(((y - (rect.bottom - edge)) / (2 * edge)) * SCROLL_MAX_STEP);
        }
      }
      session.scrollRaf = requestAnimationFrame(tick);
    };
    session.scrollRaf = requestAnimationFrame(tick);
  }, []);

  /** Drag ended without a drop in the tree: maybe a tear-off past the window edge. */
  const endSession = useCallback((session: Session, e: { clientX: number; clientY: number; screenX: number; screenY: number }) => {
    if (sessionRef.current !== session) return;
    track(session, e);
    const moving = session.sources;
    const point = session.point;
    finish(session);
    const outside = optionsRef.current.dropOutside;
    if (!outside?.onTearOff || !outside.accepts(moving)) return;
    const zone = classifySidebarDragOut(point);
    if (zone?.kind === "outside") outside.onTearOff(moving, { screenX: zone.screenX, screenY: zone.screenY });
  }, [finish, track]);

  const onDragStart = useCallback((e: ReactDragEvent) => {
    const root = rootRef.current;
    const target = e.target instanceof Element ? e.target : null;
    if (!root || !target || target.closest("input, textarea, [contenteditable='true']")) return;
    const row = target.closest<HTMLElement>(`[${TREE_ROW_ATTR}]`);
    if (!row || !root.contains(row)) return;
    const opts = optionsRef.current;
    const id = row.getAttribute(TREE_ROW_ATTR) || "";
    if (sessionRef.current) finish(sessionRef.current);
    const model = opts.getModel();
    const canDrag = opts.canDrag ?? (() => true);
    const sources = id && canDrag(id)
      ? topLevelSources(model, opts.dragSources(id)).filter((source) => canDrag(source))
      : [];
    if (!sources.length) {
      e.preventDefault();
      return;
    }
    e.stopPropagation();
    const dt = e.dataTransfer;
    let preview: HTMLElement | null = null;
    if (dt) {
      dt.effectAllowed = "move";
      // Own type: editors and other windows never read this as a tab or text drop.
      dt.setData(TREE_DRAG_MIME, opts.dragData?.(sources) ?? "tree");
      if (typeof dt.setDragImage === "function") {
        const label = opts.labelFor?.(id) || row.getAttribute("aria-label") || row.textContent?.trim() || "";
        const shown = buildPreview(row, label, sources.length);
        preview = shown;
        dt.setDragImage(shown, 14, 12);
        // The browser has taken its picture once this handler returns.
        window.setTimeout(() => shown.remove(), 0);
      }
    }
    beginTreeDrag();
    const sourceEls = sources
      .map((source) => findTreeElement(root, TREE_NODE_ATTR, source) ?? findTreeElement(root, TREE_ROW_ATTR, source))
      .filter((el): el is HTMLElement => el !== null);
    const session: Session = {
      root,
      sources,
      model,
      target: null,
      decor: new TreeDropDecor(),
      sourceEls,
      expandId: null,
      expandTimer: 0,
      scrollEl: scrollParent(row),
      scrollRaf: 0,
      point: pointOf(e) ?? { clientX: 0, clientY: 0, screenX: 0, screenY: 0 },
      editor: null,
      preview,
      detach: () => {},
    };

    // The pointer left the tree: editor panes take the drop only when it means something.
    const onDocDragOver = (event: DragEvent) => {
      if (sessionRef.current !== session) return;
      track(session, event);
      if (event.target instanceof Node && session.root.contains(event.target)) return;
      session.target = null;
      session.decor.clear();
      scheduleExpand(session, null);
      const outside = optionsRef.current.dropOutside;
      const editor = outside?.onEditor && outside.accepts(session.sources)
        ? editorDropForTreeDrag(session.point, session.root)
        : null;
      session.editor = editor;
      setSidebarEditorDropPreview(editor);
      if (editor) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      }
    };
    const onDocDrop = (event: DragEvent) => {
      if (sessionRef.current !== session) return;
      if (event.target instanceof Node && session.root.contains(event.target)) return;
      const editor = session.editor;
      const outside = optionsRef.current.dropOutside;
      if (!editor || !outside?.onEditor) return;
      event.preventDefault();
      event.stopPropagation();
      const moving = session.sources;
      finish(session);
      outside.onEditor(moving, editor);
    };
    const onWindowDrag = (event: DragEvent) => {
      if (sessionRef.current === session) track(session, event);
    };
    const onWindowDragEnd = (event: DragEvent) => endSession(session, event);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && sessionRef.current === session) finish(session);
    };
    document.addEventListener("dragover", onDocDragOver, true);
    document.addEventListener("drop", onDocDrop, true);
    window.addEventListener("drag", onWindowDrag, true);
    window.addEventListener("dragend", onWindowDragEnd, true);
    window.addEventListener("keydown", onKey, true);
    session.detach = () => {
      document.removeEventListener("dragover", onDocDragOver, true);
      document.removeEventListener("drop", onDocDrop, true);
      window.removeEventListener("drag", onWindowDrag, true);
      window.removeEventListener("dragend", onWindowDragEnd, true);
      window.removeEventListener("keydown", onKey, true);
    };
    sessionRef.current = session;
    for (const el of sourceEls) el.classList.add("tree-drag-source");
    root.classList.add("is-tree-dragging");
    document.body.classList.add("tree-dragging");
    startAutoScroll(session);
  }, [endSession, finish, scheduleExpand, startAutoScroll, track]);

  /** Returns false when no tree drag is running (so other drop handlers can act). */
  const handleDragOver = useCallback((e: ReactDragEvent): boolean => {
    const session = sessionRef.current;
    if (!session) return false;
    track(session, e);
    session.editor = null;
    setSidebarEditorDropPreview(null);
    const opts = optionsRef.current;
    const model = opts.getModel();
    session.model = model;
    const policy = opts.getPolicy();
    const target = e.target instanceof Element ? e.target : null;
    let row = target?.closest<HTMLElement>(`[${TREE_ROW_ATTR}]`) ?? null;
    if (row && !session.root.contains(row)) row = null;
    if (!row) row = nearestRow(session.root, e.clientY);
    const overId = row?.getAttribute(TREE_ROW_ATTR) ?? null;
    let zone: ReturnType<typeof zoneForPointer> = "into";
    if (row && overId !== null) {
      const rect = row.getBoundingClientRect();
      zone = zoneForPointer(e.clientY - rect.top, rect.height, isBranch(model, overId), policy, policy.intoOnly?.(overId) ?? false);
    }
    const drop = resolveDropTarget(model, session.sources, overId, zone, policy);
    session.target = drop;
    session.decor.show(session.root, drop ? indicatorFor(model, session.sources, drop, policy) : null, (id) => model.parent.get(id));
    const closedBranch = overId !== null && zone === "into" && isBranch(model, overId) && policy.isExpanded !== undefined && !policy.isExpanded(overId);
    scheduleExpand(session, closedBranch ? overId : null);
    if (e.dataTransfer) e.dataTransfer.dropEffect = drop ? "move" : "none";
    if (drop) e.preventDefault();
    e.stopPropagation();
    return true;
  }, [scheduleExpand, track]);

  const handleDragEnter = useCallback((e: ReactDragEvent): boolean => {
    if (!sessionRef.current) return false;
    e.preventDefault();
    return true;
  }, []);

  const handleDragLeave = useCallback((e: ReactDragEvent): boolean => {
    const session = sessionRef.current;
    if (!session) return false;
    const next = e.relatedTarget instanceof Node ? e.relatedTarget : null;
    if (next && session.root.contains(next)) return true;
    session.target = null;
    session.decor.clear();
    scheduleExpand(session, null);
    return true;
  }, [scheduleExpand]);

  const handleDrop = useCallback((e: ReactDragEvent): boolean => {
    const session = sessionRef.current;
    if (!session) return false;
    e.preventDefault();
    e.stopPropagation();
    const target = session.target;
    const sources = session.sources;
    const model = session.model;
    finish(session);
    if (target) optionsRef.current.onDrop({ sources, target, model });
    return true;
  }, [finish]);

  const handleDragEnd = useCallback((e: ReactDragEvent) => {
    const session = sessionRef.current;
    if (session) endSession(session, e);
  }, [endSession]);

  useEffect(() => () => {
    if (sessionRef.current) finish(sessionRef.current);
  }, [finish]);

  const ref = useCallback((el: HTMLElement | null) => {
    rootRef.current = el;
  }, []);

  const rootProps = useMemo(() => ({
    ref,
    onDragStart,
    onDragEnter: (e: ReactDragEvent) => { handleDragEnter(e); },
    onDragOver: (e: ReactDragEvent) => { handleDragOver(e); },
    onDragLeave: (e: ReactDragEvent) => { handleDragLeave(e); },
    onDrop: (e: ReactDragEvent) => { handleDrop(e); },
    onDragEnd: handleDragEnd,
  }), [ref, onDragStart, handleDragEnter, handleDragOver, handleDragLeave, handleDrop, handleDragEnd]);

  return {
    rootProps,
    rootRef,
    handleDragOver,
    handleDragEnter,
    handleDragLeave,
    handleDrop,
    isDragging: () => sessionRef.current !== null,
  };
}

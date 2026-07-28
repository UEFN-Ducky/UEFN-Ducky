import type { EditorTab } from "../types/panel";

/** Custom MIME so tab drags never paste tab ids into editors as plain text. */
export const EDITOR_TAB_DRAG_MIME = "application/x-ducky-editor-tab";
export const EDITOR_TAB_GROUP_DRAG_MIME = "application/x-ducky-editor-tab-group";
/** Settings → Plans nest drag — must not arm editor tab split overlays. */
export const PLAN_NEST_DRAG_MIME = "application/x-ducky-plan-nest";
/** text/plain JSON marker — the ONLY payload WebView2 reliably carries BETWEEN windows. */
const CROSS_WINDOW_KEY = "duckyTab";

function dndLog(event: string, detail?: unknown): void {
  console.info(`[tab-dnd] ${event}`, detail ?? "");
}

type DragSession = {
  tabId: string;
  groupId: string;
  dropped: boolean;
  lastScreenX: number;
  lastScreenY: number;
  lastClientX: number;
  lastClientY: number;
};

/** WebView2 often omits custom MIME types from dataTransfer.types during dragover. */
let dragSession: DragSession | null = null;

export function beginEditorTabDrag(tabId: string, groupId: string): void {
  dragSession = {
    tabId,
    groupId,
    dropped: false,
    lastScreenX: 0,
    lastScreenY: 0,
    lastClientX: 0,
    lastClientY: 0,
  };
}

export function endEditorTabDrag(): void {
  dragSession = null;
}

export function markEditorTabDropped(): void {
  if (dragSession) dragSession.dropped = true;
}

export function wasEditorTabDropped(): boolean {
  return dragSession?.dropped ?? false;
}

export function trackEditorTabDragPointer(e: { screenX: number; screenY: number; clientX: number; clientY: number }): void {
  if (!dragSession) return;
  // WebView2 reports dragend at 0,0 whenever the drop landed outside this window.
  // Recording that erases where the tab was actually let go, which is the one thing
  // the tear-off needs — keep the last real position instead.
  if (!e.screenX && !e.screenY && !e.clientX && !e.clientY) return;
  dragSession.lastScreenX = e.screenX;
  dragSession.lastScreenY = e.screenY;
  dragSession.lastClientX = e.clientX;
  dragSession.lastClientY = e.clientY;
}

export function getActiveEditorTabDrag(): { tabId: string; groupId: string } | null {
  if (!dragSession) return null;
  return { tabId: dragSession.tabId, groupId: dragSession.groupId };
}

export function setEditorTabDragData(dataTransfer: DataTransfer, tab: EditorTab, groupId: string): void {
  beginEditorTabDrag(tab.id, groupId);
  dataTransfer.clearData();
  dataTransfer.setData(EDITOR_TAB_DRAG_MIME, tab.id);
  dataTransfer.setData(EDITOR_TAB_GROUP_DRAG_MIME, groupId);
  // Full tab as text/plain: custom MIMEs do NOT survive WebView2 window boundaries,
  // so cross-window drops reconstruct the tab from this instead.
  dataTransfer.setData("text/plain", JSON.stringify({ [CROSS_WINDOW_KEY]: tab }));
  dataTransfer.effectAllowed = "copyMove";
  dndLog("dragstart", { tabId: tab.id, groupId });
}

let externalDropHandler: ((tab: EditorTab) => void) | null = null;

/** Each window registers ONE handler that opens a tab dropped from another window. */
export function setExternalTabDropHandler(fn: (tab: EditorTab) => void): () => void {
  externalDropHandler = fn;
  return () => {
    if (externalDropHandler === fn) externalDropHandler = null;
  };
}

/** True when the drop came from ANOTHER window and was handled (tab opened here;
 * the tab-registry claim then closes the source window's copy = move semantics). */
export function handleExternalTabDrop(e: { dataTransfer: DataTransfer }): boolean {
  if (dragSession) return false; // local drag — normal in-window path handles it
  let tab: EditorTab | null = null;
  try {
    tab = JSON.parse(e.dataTransfer.getData("text/plain") || "{}")[CROSS_WINDOW_KEY] ?? null;
  } catch {
    tab = null;
  }
  if (!tab || typeof tab.id !== "string" || !tab.id) {
    dndLog("drop ignored — no cross-window tab payload", {
      types: Array.from(e.dataTransfer.types),
    });
    return false;
  }
  dndLog("cross-window drop", { tabId: tab.id, handler: !!externalDropHandler });
  externalDropHandler?.(tab);
  return true;
}

export function getEditorTabDragData(dataTransfer: DataTransfer): string {
  const fromTransfer = dataTransfer.getData(EDITOR_TAB_DRAG_MIME);
  if (fromTransfer) return fromTransfer;
  return dragSession?.tabId ?? "";
}

export function getEditorTabGroupDragData(dataTransfer: DataTransfer): string {
  const fromTransfer = dataTransfer.getData(EDITOR_TAB_GROUP_DRAG_MIME);
  if (fromTransfer) return fromTransfer;
  return dragSession?.groupId ?? "";
}

export function isEditorTabDrag(e?: { dataTransfer?: DataTransfer }): boolean {
  if (dragSession) return true;
  if (!e?.dataTransfer) return false;
  const types = Array.from(e.dataTransfer.types);
  // Plan nest (and similar) also set text/plain — never treat those as tab splits.
  if (types.includes(PLAN_NEST_DRAG_MIME)) return false;
  if (types.includes(EDITOR_TAB_DRAG_MIME)) return true;
  // Cross-window tab drags surface only text/plain in WebView2.
  if (!types.includes("text/plain")) return false;
  try {
    const raw = e.dataTransfer.getData("text/plain");
    // During dragover, getData is often ""; allow text/plain-only for cross-window.
    if (!raw) return true;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return Boolean(parsed && typeof parsed === "object" && CROSS_WINDOW_KEY in parsed);
  } catch {
    return false;
  }
}

export function getLastDragScreenPoint(): { screenX: number; screenY: number } | null {
  if (!dragSession) return null;
  const { lastScreenX, lastScreenY, lastClientX, lastClientY } = dragSession;
  if (lastScreenX !== 0 || lastScreenY !== 0) {
    return { screenX: lastScreenX, screenY: lastScreenY };
  }
  if (lastClientX < 0 || lastClientY < 0 || lastClientX > window.innerWidth || lastClientY > window.innerHeight) {
    return {
      screenX: window.screenX + lastClientX,
      screenY: window.screenY + lastClientY,
    };
  }
  return null;
}


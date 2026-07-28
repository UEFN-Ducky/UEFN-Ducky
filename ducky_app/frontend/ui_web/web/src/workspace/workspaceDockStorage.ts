import {
  OUTLINE_PANEL_WIDTH_DEFAULT,
  OUTLINE_PANEL_WIDTH_MAX,
  OUTLINE_PANEL_WIDTH_MIN,
} from "../hooks/useOutlinePanelWidth";
import {
  SIDEBAR_WIDTH_DEFAULT,
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
} from "../hooks/useSidebarWidth";
import { getApi } from "../hooks/usePanelApi";

export type DockPanelId = "chats" | "files" | "outline" | "history" | "tester" | "groupchat" | "discordhub";
export type DockSide = "left" | "right";
/** How the panels docked on a rail lay out: one tab bar, or a resizable vertical stack. */
export type DockPanelMode = "tabs" | "stacked";

// `discordhub` was merged into `groupchat` (one Discord panel) — kept in the type
// union for saved-layout compatibility but no longer a rendered panel.
export const ALL_DOCK_PANELS: DockPanelId[] = ["chats", "files", "outline", "history", "tester", "groupchat"];
export const MIN_DOCK_PANEL_HEIGHT = 80;

export const DOCK_STORAGE_KEY = "uefn-workspace-dock-layout";
export const DOCK_CHANGE_EVENT = "uefn-workspace-dock-layout";

export function dockStorageKey(windowId: string): string {
  return `${DOCK_STORAGE_KEY}:${windowId}`;
}

export function isFocusWindowId(windowId: string): boolean {
  return windowId !== "main";
}

export type DockRailStackState = {
  order: DockPanelId[];
  splitRatio: number;
  /** Per-panel flex weights when 3+ panels are stacked (normalized on read). */
  panelFlex?: Partial<Record<DockPanelId, number>>;
  /** Vertical split between sidebar (chats/files) and verse (outline/history) sections in a mixed rail. */
  familySplitRatio?: number;
  collapsed: Record<DockPanelId, boolean>;
  focusedPanel: DockPanelId;
};

export type WorkspaceDockSnapshot = {
  version: 1;
  panelSide: Record<DockPanelId, DockSide>;
  left: DockRailStackState;
  right: DockRailStackState;
  leftRailOpen: boolean;
  rightRailOpen: boolean;
  leftWidth: number;
  rightWidth: number;
  /** Layout mode for whatever panels sit on the left rail. */
  leftPanelMode: DockPanelMode;
  /** Layout mode for whatever panels sit on the right rail. */
  rightPanelMode: DockPanelMode;
};

function clampLeftWidth(value: number): number {
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, value));
}

function clampRightWidth(value: number): number {
  return Math.min(OUTLINE_PANEL_WIDTH_MAX, Math.max(OUTLINE_PANEL_WIDTH_MIN, value));
}

function defaultCollapsed(): Record<DockPanelId, boolean> {
  return {
    chats: false,
    files: false,
    outline: false,
    history: false,
    tester: false,
    groupchat: false,
    discordhub: false,
  };
}

function defaultRailStack(order: DockPanelId[], focused: DockPanelId, splitRatio = 0.5): DockRailStackState {
  return {
    order,
    splitRatio,
    familySplitRatio: 0.5,
    collapsed: defaultCollapsed(),
    focusedPanel: focused,
  };
}

export function defaultDockSnapshot(): WorkspaceDockSnapshot {
  return {
    version: 1,
    panelSide: {
      chats: "left",
      files: "left",
      outline: "right",
      history: "right",
      tester: "right",
      groupchat: "left",
      discordhub: "left",
    },
    left: defaultRailStack(["chats", "files", "groupchat"], "chats", 0.5),
    right: defaultRailStack(["outline", "history", "tester"], "outline", 0.55),
    leftRailOpen: true,
    rightRailOpen: true,
    leftWidth: SIDEBAR_WIDTH_DEFAULT,
    rightWidth: OUTLINE_PANEL_WIDTH_DEFAULT,
    leftPanelMode: "stacked",
    rightPanelMode: "stacked",
  };
}

export function defaultFocusDockSnapshot(): WorkspaceDockSnapshot {
  return {
    version: 1,
    panelSide: {
      chats: "left",
      files: "left",
      outline: "right",
      history: "right",
      tester: "right",
      groupchat: "right",
      discordhub: "left",
    },
    left: defaultRailStack(["outline", "history", "tester"], "outline", 0.55),
    right: defaultRailStack(["outline", "history", "tester"], "outline", 0.55),
    leftRailOpen: false,
    rightRailOpen: false,
    leftWidth: OUTLINE_PANEL_WIDTH_DEFAULT,
    rightWidth: OUTLINE_PANEL_WIDTH_DEFAULT,
    leftPanelMode: "stacked",
    rightPanelMode: "stacked",
  };
}

function defaultSnapshotForWindow(windowId: string): WorkspaceDockSnapshot {
  return isFocusWindowId(windowId) ? defaultFocusDockSnapshot() : defaultDockSnapshot();
}

function readLegacyJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function readLegacyNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  } catch {
    return fallback;
  }
}

function migrateFromLegacy(): WorkspaceDockSnapshot {
  const base = defaultDockSnapshot();

  const sidebarOrder = readLegacyJson<DockPanelId[]>("uefn-sidebar-panel-order");
  if (sidebarOrder?.includes("chats") && sidebarOrder.includes("files")) {
    base.left.order = sidebarOrder.filter((id) => id === "chats" || id === "files") as DockPanelId[];
  }

  const outlineOrder = readLegacyJson<("outline" | "history")[]>("uefn-verse-outline-panel-order");
  if (outlineOrder?.includes("outline") && outlineOrder.includes("history")) {
    base.right.order = [...outlineOrder];
  }

  const sidebarSplit = readLegacyNumber("uefn-sidebar-panel-split", 0.5);
  base.left.splitRatio = Math.min(0.85, Math.max(0.15, sidebarSplit));

  const outlineSplit = readLegacyNumber("uefn-verse-outline-panel-split", 0.55);
  base.right.splitRatio = Math.min(0.85, Math.max(0.15, outlineSplit));

  const sidebarCollapsed = readLegacyJson<Partial<Record<"chats" | "files", boolean>>>(
    "uefn-sidebar-panel-collapsed",
  );
  if (sidebarCollapsed) {
    base.left.collapsed.chats = !!sidebarCollapsed.chats;
    base.left.collapsed.files = !!sidebarCollapsed.files;
  }

  const outlineCollapsed = readLegacyJson<Partial<Record<"outline" | "history", boolean>>>(
    "uefn-verse-outline-panel-collapsed",
  );
  if (outlineCollapsed) {
    base.right.collapsed.outline = !!outlineCollapsed.outline;
    base.right.collapsed.history = !!outlineCollapsed.history;
  }

  base.leftWidth = clampLeftWidth(readLegacyNumber("uefn-panel-sidebar-width", SIDEBAR_WIDTH_DEFAULT));
  base.rightWidth = clampRightWidth(readLegacyNumber("uefn-panel-outline-width", OUTLINE_PANEL_WIDTH_DEFAULT));

  // Legacy modes were keyed by panel family; map them onto the rails those
  // families sit on by default (sidebar → left, verse → right).
  const sidebarMode = localStorage.getItem("uefn-sidebar-panel-mode");
  if (sidebarMode === "tabs" || sidebarMode === "stacked") base.leftPanelMode = sidebarMode;

  const verseMode = localStorage.getItem("uefn-verse-outline-panel-mode");
  if (verseMode === "tabs" || verseMode === "stacked") base.rightPanelMode = verseMode;

  return base;
}

export function normalizeSnapshot(raw: unknown, windowId: string): WorkspaceDockSnapshot {
  const base = defaultSnapshotForWindow(windowId);
  if (!raw || typeof raw !== "object") return base;
  const data = raw as Partial<WorkspaceDockSnapshot>;

  if (data.panelSide) {
    for (const id of ALL_DOCK_PANELS) {
      const side = data.panelSide[id];
      if (side === "left" || side === "right") base.panelSide[id] = side;
    }
  }

  const normalizeStack = (stack: Partial<DockRailStackState> | undefined, fallback: DockRailStackState) => {
    if (!stack) return fallback;
    const order = Array.isArray(stack.order)
      ? stack.order.filter((id): id is DockPanelId => ALL_DOCK_PANELS.includes(id as DockPanelId))
      : fallback.order;
    const splitRatio =
      typeof stack.splitRatio === "number" && Number.isFinite(stack.splitRatio)
        ? Math.min(0.85, Math.max(0.15, stack.splitRatio))
        : fallback.splitRatio;
    const familySplitRatio =
      typeof stack.familySplitRatio === "number" && Number.isFinite(stack.familySplitRatio)
        ? Math.min(0.85, Math.max(0.15, stack.familySplitRatio))
        : fallback.familySplitRatio;
    const panelFlex =
      stack.panelFlex && typeof stack.panelFlex === "object"
        ? Object.fromEntries(
            Object.entries(stack.panelFlex).filter(
              ([id, value]) =>
                ALL_DOCK_PANELS.includes(id as DockPanelId) &&
                typeof value === "number" &&
                Number.isFinite(value) &&
                value > 0,
            ),
          )
        : fallback.panelFlex;
    const collapsed = { ...defaultCollapsed(), ...stack.collapsed };
    const focused =
      stack.focusedPanel && ALL_DOCK_PANELS.includes(stack.focusedPanel)
        ? stack.focusedPanel
        : order[0] ?? fallback.focusedPanel;
    return { order, splitRatio, panelFlex, familySplitRatio, collapsed, focusedPanel: focused };
  };

  base.left = normalizeStack(data.left, base.left);
  base.right = normalizeStack(data.right, base.right);

  if (typeof data.leftRailOpen === "boolean") base.leftRailOpen = data.leftRailOpen;
  if (typeof data.rightRailOpen === "boolean") base.rightRailOpen = data.rightRailOpen;
  if (typeof data.leftWidth === "number") base.leftWidth = clampLeftWidth(data.leftWidth);
  if (typeof data.rightWidth === "number") base.rightWidth = clampRightWidth(data.rightWidth);

  // Accept the current per-rail keys, falling back to the pre-refactor
  // per-family keys (sidebar → left, verse → right) for saved snapshots.
  const legacyModes = data as { sidebarPanelMode?: unknown; verseAuxPanelMode?: unknown };
  const leftMode = data.leftPanelMode ?? legacyModes.sidebarPanelMode;
  if (leftMode === "tabs" || leftMode === "stacked") base.leftPanelMode = leftMode;
  const rightMode = data.rightPanelMode ?? legacyModes.verseAuxPanelMode;
  if (rightMode === "tabs" || rightMode === "stacked") base.rightPanelMode = rightMode;

  return base;
}

export function readDockSnapshot(windowId = "main"): WorkspaceDockSnapshot {
  const key = dockStorageKey(windowId);
  try {
    let raw = localStorage.getItem(key);
    if (!raw && windowId === "main") {
      raw = localStorage.getItem(DOCK_STORAGE_KEY);
      if (raw) {
        const snapshot = normalizeSnapshot(JSON.parse(raw), windowId);
        persistDockSnapshot(snapshot, windowId);
        return snapshot;
      }
    }
    if (!raw) {
      if (windowId === "main") {
        const migrated = migrateFromLegacy();
        persistDockSnapshot(migrated, windowId);
        return migrated;
      }
      const defaults = defaultFocusDockSnapshot();
      persistDockSnapshot(defaults, windowId);
      return defaults;
    }
    return normalizeSnapshot(JSON.parse(raw), windowId);
  } catch {
    if (windowId === "main") {
      const migrated = migrateFromLegacy();
      persistDockSnapshot(migrated, windowId);
      return migrated;
    }
    const defaults = defaultFocusDockSnapshot();
    persistDockSnapshot(defaults, windowId);
    return defaults;
  }
}

function writeLocalDockSnapshot(snapshot: WorkspaceDockSnapshot, windowId: string): boolean {
  try {
    const key = dockStorageKey(windowId);
    const encoded = JSON.stringify(snapshot);
    if (localStorage.getItem(key) === encoded) return false;
    localStorage.setItem(key, encoded);
    return true;
  } catch {
    return false;
  }
}

/** Sync localStorage only — used when hydrating from AppData so we don't echo-write disk. */
export function syncLocalDockSnapshot(snapshot: WorkspaceDockSnapshot, windowId = "main"): void {
  if (writeLocalDockSnapshot(snapshot, windowId)) {
    window.dispatchEvent(new CustomEvent(DOCK_CHANGE_EVENT, { detail: { windowId } }));
  }
}

const _diskTimers = new Map<string, number>();
const _pendingDisk = new Map<string, WorkspaceDockSnapshot>();

function writeDockSnapshotToDisk(snapshot: WorkspaceDockSnapshot, windowId: string): void {
  try {
    const api = getApi();
    if (api?.save_workspace_dock) {
      void api.save_workspace_dock({
        window_id: windowId,
        snapshot: snapshot as unknown as Record<string, unknown>,
      });
    }
  } catch {
    // ignore bridge errors
  }
}

function persistDockSnapshotToDisk(snapshot: WorkspaceDockSnapshot, windowId: string): void {
  // Debounce — rail resize commits every pointer move; AppData only needs the settle.
  _pendingDisk.set(windowId, snapshot);
  const prev = _diskTimers.get(windowId);
  if (prev !== undefined) window.clearTimeout(prev);
  const handle = window.setTimeout(() => {
    _diskTimers.delete(windowId);
    const pending = _pendingDisk.get(windowId);
    _pendingDisk.delete(windowId);
    if (pending) writeDockSnapshotToDisk(pending, windowId);
  }, 250);
  _diskTimers.set(windowId, handle);
}

/** Flush any pending debounced AppData write (call on window hide/close). */
export function flushDockSnapshotToDisk(windowId = "main"): void {
  const prev = _diskTimers.get(windowId);
  if (prev !== undefined) window.clearTimeout(prev);
  _diskTimers.delete(windowId);
  const pending = _pendingDisk.get(windowId);
  _pendingDisk.delete(windowId);
  if (pending) writeDockSnapshotToDisk(pending, windowId);
}

export function persistDockSnapshot(snapshot: WorkspaceDockSnapshot, windowId = "main"): void {
  const changed = writeLocalDockSnapshot(snapshot, windowId);
  if (changed) {
    window.dispatchEvent(new CustomEvent(DOCK_CHANGE_EVENT, { detail: { windowId } }));
  }
  persistDockSnapshotToDisk(snapshot, windowId);
}

export function panelsOnSide(snapshot: WorkspaceDockSnapshot, side: DockSide): DockPanelId[] {
  const ids = ALL_DOCK_PANELS.filter((id) => snapshot.panelSide[id] === side);
  const stack = side === "left" ? snapshot.left : snapshot.right;
  const ordered = stack.order.filter((id) => ids.includes(id));
  for (const id of ids) {
    if (!ordered.includes(id)) ordered.push(id);
  }
  return ordered;
}

/**
 * The single panel that is "active" on a rail — the one whose body is visible
 * in tabs mode. Keyed by side, never by panel family: the focused panel if it
 * is present in this (already variant-filtered) list, otherwise the first
 * panel. Every body renderer on the side must agree on this so exactly one
 * shows; pass the same list the tab bar renders.
 */
export function activePanelForList(
  panelIds: DockPanelId[],
  focusedPanel: DockPanelId,
): DockPanelId | undefined {
  return panelIds.includes(focusedPanel) ? focusedPanel : panelIds[0];
}

import { useCallback, useEffect, useState } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import {
  DOCK_CHANGE_EVENT,
  dockStorageKey,
  readDockSnapshot,
  persistDockSnapshot,
} from "../workspace/workspaceDockStorage";

export type SidebarPanelMode = "tabs" | "stacked";
export type SidebarPanelId = "chats" | "files";

const LEGACY_STORAGE_KEY = "uefn-sidebar-panel-mode";
const LEGACY_CHANGE_EVENT = "uefn-sidebar-panel-mode";

function readMode(): SidebarPanelMode {
  try {
    // Non-docked sidebar maps onto the left rail.
    return readDockSnapshot(WINDOW_ID).leftPanelMode;
  } catch {
    return "stacked";
  }
}

export function useSidebarPanelMode() {
  const [mode, setModeState] = useState<SidebarPanelMode>(readMode);
  const storageKey = dockStorageKey(WINDOW_ID);

  useEffect(() => {
    const refresh = () => setModeState(readMode());
    const onStorage = (e: StorageEvent) => {
      if (e.key === storageKey || e.key === LEGACY_STORAGE_KEY) refresh();
    };
    const onDockChange = (e: Event) => {
      const detail = (e as CustomEvent<{ windowId?: string }>).detail;
      if (!detail?.windowId || detail.windowId === WINDOW_ID) refresh();
    };
    const onLegacy = () => refresh();
    window.addEventListener("storage", onStorage);
    window.addEventListener(DOCK_CHANGE_EVENT, onDockChange);
    window.addEventListener(LEGACY_CHANGE_EVENT, onLegacy);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(DOCK_CHANGE_EVENT, onDockChange);
      window.removeEventListener(LEGACY_CHANGE_EVENT, onLegacy);
    };
  }, [storageKey]);

  const setMode = useCallback((next: SidebarPanelMode) => {
    const snapshot = readDockSnapshot(WINDOW_ID);
    if (snapshot.leftPanelMode === next) return;
    persistDockSnapshot({ ...snapshot, leftPanelMode: next }, WINDOW_ID);
    try {
      localStorage.setItem(LEGACY_STORAGE_KEY, next);
    } catch {
      // ignore storage errors
    }
    setModeState(next);
    window.dispatchEvent(new CustomEvent(LEGACY_CHANGE_EVENT));
  }, []);

  return { mode, setMode };
}

import { useCallback, useEffect, useState } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import {
  DOCK_CHANGE_EVENT,
  dockStorageKey,
  readDockSnapshot,
  persistDockSnapshot,
} from "../workspace/workspaceDockStorage";

export type VerseOutlinePanelMode = "tabs" | "stacked";

const LEGACY_STORAGE_KEY = "uefn-verse-outline-panel-mode";
const LEGACY_CHANGE_EVENT = "uefn-verse-outline-panel-mode";

function readMode(): VerseOutlinePanelMode {
  try {
    // Non-docked outline/history maps onto the right rail.
    return readDockSnapshot(WINDOW_ID).rightPanelMode;
  } catch {
    return "stacked";
  }
}

export function useVerseOutlinePanelMode() {
  const [mode, setModeState] = useState<VerseOutlinePanelMode>(readMode);
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

  const setMode = useCallback((next: VerseOutlinePanelMode) => {
    const snapshot = readDockSnapshot(WINDOW_ID);
    if (snapshot.rightPanelMode === next) return;
    persistDockSnapshot({ ...snapshot, rightPanelMode: next }, WINDOW_ID);
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

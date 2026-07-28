import { useCallback, useEffect, useState } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import {
  DOCK_CHANGE_EVENT,
  dockStorageKey,
  readDockSnapshot,
  persistDockSnapshot,
  type DockPanelMode,
  type DockSide,
} from "../workspace/workspaceDockStorage";

const FIELD = { left: "leftPanelMode", right: "rightPanelMode" } as const;

function readMode(side: DockSide): DockPanelMode {
  try {
    return readDockSnapshot(WINDOW_ID)[FIELD[side]];
  } catch {
    return "stacked";
  }
}

/**
 * Layout mode (tabs vs stacked) for a dock rail, keyed by side rather than by
 * panel family. Whatever panels are docked on the side follow this mode.
 */
export function useDockSidePanelMode(side: DockSide) {
  const [mode, setModeState] = useState<DockPanelMode>(() => readMode(side));
  const storageKey = dockStorageKey(WINDOW_ID);

  useEffect(() => {
    const refresh = () => setModeState(readMode(side));
    const onStorage = (e: StorageEvent) => {
      if (e.key === storageKey) refresh();
    };
    const onDockChange = (e: Event) => {
      const detail = (e as CustomEvent<{ windowId?: string }>).detail;
      if (!detail?.windowId || detail.windowId === WINDOW_ID) refresh();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(DOCK_CHANGE_EVENT, onDockChange);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(DOCK_CHANGE_EVENT, onDockChange);
    };
  }, [storageKey, side]);

  const setMode = useCallback(
    (next: DockPanelMode) => {
      const snapshot = readDockSnapshot(WINDOW_ID);
      if (snapshot[FIELD[side]] === next) return;
      persistDockSnapshot({ ...snapshot, [FIELD[side]]: next }, WINDOW_ID);
      setModeState(next);
    },
    [side],
  );

  return { mode, setMode };
}

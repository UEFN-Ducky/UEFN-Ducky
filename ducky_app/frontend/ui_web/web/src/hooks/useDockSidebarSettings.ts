import { useCallback, useEffect, useState } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import {
  DOCK_CHANGE_EVENT,
  dockStorageKey,
  persistDockSnapshot,
  readDockSnapshot,
  withPanelOnSide,
  withRailEnabled,
  type DockPanelId,
  type DockSide,
  type WorkspaceDockSnapshot,
} from "../workspace/workspaceDockStorage";

function isOnSide(snapshot: WorkspaceDockSnapshot, panelId: DockPanelId, side: DockSide): boolean {
  return !snapshot.hiddenPanels.includes(panelId) && snapshot.panelSide[panelId] === side;
}

/** Appearance → Sidebar reads/writes the dock snapshot directly (settings overlay has no dock provider). */
export function useDockSidebarSettings() {
  const [snapshot, setSnapshot] = useState(() => readDockSnapshot(WINDOW_ID));
  const storageKey = dockStorageKey(WINDOW_ID);

  useEffect(() => {
    const refresh = () => setSnapshot(readDockSnapshot(WINDOW_ID));
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
  }, [storageKey]);

  const setRailEnabled = useCallback((side: DockSide, enabled: boolean) => {
    persistDockSnapshot(withRailEnabled(readDockSnapshot(WINDOW_ID), side, enabled), WINDOW_ID);
  }, []);

  const setPanelOnSide = useCallback((panelId: DockPanelId, targetSide: DockSide | null) => {
    persistDockSnapshot(withPanelOnSide(readDockSnapshot(WINDOW_ID), panelId, targetSide), WINDOW_ID);
  }, []);

  return {
    snapshot,
    leftRailEnabled: snapshot.leftRailEnabled !== false,
    rightRailEnabled: snapshot.rightRailEnabled !== false,
    isOnSide: (panelId: DockPanelId, side: DockSide) => isOnSide(snapshot, panelId, side),
    setRailEnabled,
    setPanelOnSide,
  };
}

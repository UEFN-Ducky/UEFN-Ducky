import { useCallback, useEffect, useState } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import {
  DOCK_CHANGE_EVENT,
  dockStorageKey,
  panelsOnSide,
  persistDockSnapshot,
  readDockSnapshot,
} from "../workspace/workspaceDockStorage";

function readRightRailState() {
  const snapshot = readDockSnapshot(WINDOW_ID);
  return {
    open: snapshot.rightRailOpen,
    hasPanels: panelsOnSide(snapshot, "right").length > 0,
  };
}

export function useRightRailOpen() {
  const [state, setState] = useState(readRightRailState);

  useEffect(() => {
    const sync = () => setState(readRightRailState());
    const onStorage = (e: StorageEvent) => {
      if (e.key === dockStorageKey(WINDOW_ID)) sync();
    };
    const onCustom = (e: Event) => {
      const detail = (e as CustomEvent<{ windowId?: string }>).detail;
      if (detail?.windowId !== WINDOW_ID) return;
      sync();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(DOCK_CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(DOCK_CHANGE_EVENT, onCustom);
    };
  }, []);

  const toggleRightRail = useCallback(() => {
    const snapshot = readDockSnapshot(WINDOW_ID);
    persistDockSnapshot({ ...snapshot, rightRailOpen: !snapshot.rightRailOpen }, WINDOW_ID);
  }, []);

  return {
    rightRailOpen: state.open,
    hasRightPanels: state.hasPanels,
    toggleRightRail,
  };
}

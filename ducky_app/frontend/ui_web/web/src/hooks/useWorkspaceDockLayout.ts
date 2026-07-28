import { useCallback, useEffect, useRef, useState } from "react";
import {
  MIN_DOCK_PANEL_HEIGHT,
  dockStorageKey,
  type DockPanelId,
  type DockPanelMode,
  type DockSide,
  type WorkspaceDockSnapshot,
  flushDockSnapshotToDisk,
  normalizeSnapshot,
  panelsOnSide,
  persistDockSnapshot,
  readDockSnapshot,
  syncLocalDockSnapshot,
  DOCK_CHANGE_EVENT,
} from "../workspace/workspaceDockStorage";
import { resizeStackedPanelSplit } from "../utils/stackedPanelFlex";
import { onApiReady } from "./onApiReady";

export type { DockPanelId, DockPanelMode, DockSide, WorkspaceDockSnapshot };

export function useWorkspaceDockLayout(windowId: string) {
  const storageKey = dockStorageKey(windowId);
  const [snapshot, setSnapshot] = useState<WorkspaceDockSnapshot>(() => readDockSnapshot(windowId));
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const hydratingRef = useRef(false);

  // Prefer AppData over localStorage once the bridge is up.
  useEffect(() => {
    return onApiReady((api) => {
      if (!api.get_workspace_dock) return;
      hydratingRef.current = true;
      void api
        .get_workspace_dock(windowId)
        .then((raw) => {
          if (raw && typeof raw === "object" && Object.keys(raw).length > 0) {
            const next = normalizeSnapshot(raw, windowId);
            setSnapshot((prev) =>
              JSON.stringify(prev) === JSON.stringify(next) ? prev : next,
            );
            syncLocalDockSnapshot(next, windowId);
            return;
          }
          // First run after upgrade: seed disk from whatever localStorage had.
          void api.save_workspace_dock?.({
            window_id: windowId,
            snapshot: snapshotRef.current as unknown as Record<string, unknown>,
          });
        })
        .finally(() => {
          hydratingRef.current = false;
        });
    });
  }, [windowId]);

  useEffect(() => {
    const flush = () => {
      persistDockSnapshot(snapshotRef.current, windowId);
      flushDockSnapshotToDisk(windowId);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("pagehide", flush);
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("beforeunload", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [windowId]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== storageKey) return;
      setSnapshot((prev) => {
        const next = readDockSnapshot(windowId);
        return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
      });
    };
    const onCustom = (e: Event) => {
      if (hydratingRef.current) return;
      const detail = (e as CustomEvent<{ windowId?: string }>).detail;
      if (detail?.windowId !== windowId) return;
      setSnapshot((prev) => {
        const next = readDockSnapshot(windowId);
        return JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
      });
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(DOCK_CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(DOCK_CHANGE_EVENT, onCustom);
    };
  }, [storageKey, windowId]);

  const commit = useCallback(
    (updater: (prev: WorkspaceDockSnapshot) => WorkspaceDockSnapshot) => {
      setSnapshot((prev) => {
        const next = updater(prev);
        if (next !== prev) {
          persistDockSnapshot(next, windowId);
        }
        return next;
      });
    },
    [windowId],
  );

  const leftPanels = panelsOnSide(snapshot, "left");
  const rightPanels = panelsOnSide(snapshot, "right");

  const setLeftRailOpen = useCallback(
    (open: boolean) => {
      commit((prev) => (prev.leftRailOpen === open ? prev : { ...prev, leftRailOpen: open }));
    },
    [commit],
  );

  const setRightRailOpen = useCallback(
    (open: boolean) => {
      commit((prev) => (prev.rightRailOpen === open ? prev : { ...prev, rightRailOpen: open }));
    },
    [commit],
  );

  const resizeRailWidth = useCallback(
    (side: DockSide, width: number) => {
      commit((prev) =>
        side === "left" ? { ...prev, leftWidth: width } : { ...prev, rightWidth: width },
      );
    },
    [commit],
  );

  const persistRailWidth = useCallback(() => {
    persistDockSnapshot(snapshotRef.current, windowId);
  }, [windowId]);

  const toggleCollapsed = useCallback(
    (side: DockSide, id: DockPanelId) => {
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const nextCollapsed = { ...stack.collapsed, [id]: !stack.collapsed[id] };
        const nextStack = { ...stack, collapsed: nextCollapsed };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const swapOrder = useCallback(
    (side: DockSide) => {
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const ids = panelsOnSide(prev, side);
        if (ids.length < 2) return prev;
        const nextOrder: DockPanelId[] = [ids[1]!, ids[0]!];
        for (const id of stack.order) {
          if (!nextOrder.includes(id) && ids.includes(id)) nextOrder.push(id);
        }
        const nextStack = { ...stack, order: nextOrder };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const swapPanels = useCallback(
    (side: DockSide, panelA: DockPanelId, panelB: DockPanelId) => {
      if (panelA === panelB) return;
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const order = [...stack.order];
        const i = order.indexOf(panelA);
        const j = order.indexOf(panelB);
        if (i < 0 || j < 0) return prev;
        order[i] = panelB;
        order[j] = panelA;
        const nextStack = { ...stack, order };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const resizeSplit = useCallback(
    (side: DockSide, splitIndex: number, deltaPx: number, containerHeight: number) => {
      if (containerHeight <= 0) return;
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const next = resizeStackedPanelSplit(
          {
            order: stack.order,
            collapsed: stack.collapsed,
            panelFlex: stack.panelFlex,
            splitRatio: stack.splitRatio,
            minPanelHeight: MIN_DOCK_PANEL_HEIGHT,
          },
          splitIndex,
          deltaPx,
          containerHeight,
        );
        const nextStack = { ...stack, panelFlex: next.panelFlex, splitRatio: next.splitRatio };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const resizeFamilySplit = useCallback(
    (side: DockSide, deltaPx: number, containerHeight: number) => {
      if (containerHeight <= 0) return;
      const deltaRatio = deltaPx / containerHeight;
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const current = stack.familySplitRatio ?? 0.5;
        const minRatio = MIN_DOCK_PANEL_HEIGHT / containerHeight;
        const maxRatio = 1 - minRatio;
        const nextRatio = Math.max(minRatio, Math.min(maxRatio, current + deltaRatio));
        const nextStack = { ...stack, familySplitRatio: nextRatio };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const persistSplit = useCallback(() => {
    persistDockSnapshot(snapshotRef.current, windowId);
  }, [windowId]);

  const setFocusedPanel = useCallback(
    (side: DockSide, id: DockPanelId) => {
      commit((prev) => {
        const stack = side === "left" ? prev.left : prev.right;
        const nextStack = { ...stack, focusedPanel: id };
        return side === "left" ? { ...prev, left: nextStack } : { ...prev, right: nextStack };
      });
    },
    [commit],
  );

  const movePanel = useCallback(
    (panelId: DockPanelId, targetSide: DockSide, insertIndex?: number) => {
      commit((prev) => {
        const fromSide = prev.panelSide[panelId];
        if (fromSide === targetSide && insertIndex === undefined) return prev;

        const next = { ...prev, panelSide: { ...prev.panelSide, [panelId]: targetSide } };

        const removeFromStack = (side: DockSide) => {
          const stack = side === "left" ? next.left : next.right;
          const order = stack.order.filter((id) => id !== panelId);
          return { ...stack, order };
        };

        let left = fromSide === "left" ? removeFromStack("left") : next.left;
        let right = fromSide === "right" ? removeFromStack("right") : next.right;

        const insertInto = (side: DockSide) => {
          const stack = side === "left" ? left : right;
          const order = stack.order.filter((id) => id !== panelId);
          const idx = insertIndex !== undefined ? Math.min(insertIndex, order.length) : order.length;
          order.splice(idx, 0, panelId);
          const focused =
            stack.focusedPanel === panelId || !order.includes(stack.focusedPanel)
              ? panelId
              : stack.focusedPanel;
          return { ...stack, order, focusedPanel: focused };
        };

        if (targetSide === "left") left = insertInto("left");
        else right = insertInto("right");

        return { ...next, left, right };
      });
    },
    [commit],
  );

  const setPanelModeForSide = useCallback(
    (side: DockSide, mode: DockPanelMode) => {
      commit((prev) => {
        if (side === "left") {
          return prev.leftPanelMode === mode ? prev : { ...prev, leftPanelMode: mode };
        }
        return prev.rightPanelMode === mode ? prev : { ...prev, rightPanelMode: mode };
      });
    },
    [commit],
  );

  const panelModeForSide = useCallback(
    (side: DockSide): DockPanelMode =>
      side === "left" ? snapshot.leftPanelMode : snapshot.rightPanelMode,
    [snapshot.leftPanelMode, snapshot.rightPanelMode],
  );

  const stackForSide = useCallback(
    (side: DockSide) => (side === "left" ? snapshot.left : snapshot.right),
    [snapshot.left, snapshot.right],
  );

  return {
    windowId,
    snapshot,
    leftPanels,
    rightPanels,
    setLeftRailOpen,
    setRightRailOpen,
    resizeRailWidth,
    persistRailWidth,
    toggleCollapsed,
    swapOrder,
    swapPanels,
    resizeSplit,
    resizeFamilySplit,
    persistSplit,
    setFocusedPanel,
    movePanel,
    setPanelModeForSide,
    panelModeForSide,
    stackForSide,
    leftRailOpen: snapshot.leftRailOpen,
    rightRailOpen: snapshot.rightRailOpen,
    leftWidth: snapshot.leftWidth,
    rightWidth: snapshot.rightWidth,
    leftPanelMode: snapshot.leftPanelMode,
    rightPanelMode: snapshot.rightPanelMode,
  };
}

export type WorkspaceDockLayoutApi = ReturnType<typeof useWorkspaceDockLayout>;

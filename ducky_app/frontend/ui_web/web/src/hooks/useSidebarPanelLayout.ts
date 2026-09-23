import { reorderStackedPanels, type StackedPanelDropEdge } from "../utils/stackedPanelDropHint";
import { useCallback, useRef, useState } from "react";
import { resizeStackedPanelSplit, type StackedPanelResizeSnapshot } from "../utils/stackedPanelFlex";
import type { SidebarPanelId } from "./useSidebarPanelMode";

export const MIN_SIDEBAR_PANEL_HEIGHT = 120;

const ORDER_KEY = "uefn-sidebar-panel-order";
const SPLIT_KEY = "uefn-sidebar-panel-split";
const COLLAPSED_KEY = "uefn-sidebar-panel-collapsed";

const DEFAULT_ORDER: SidebarPanelId[] = ["chats", "files"];

type CollapsedState = Record<SidebarPanelId, boolean>;

function readOrder(): SidebarPanelId[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY);
    if (!raw) return DEFAULT_ORDER;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed) || parsed.length !== 2) return DEFAULT_ORDER;
    if (parsed.includes("chats") && parsed.includes("files")) return parsed as SidebarPanelId[];
  } catch {
    // ignore
  }
  return DEFAULT_ORDER;
}

function readSplit(): number {
  try {
    const raw = localStorage.getItem(SPLIT_KEY);
    if (raw === null) return 0.5;
    const n = Number(raw);
    if (!Number.isFinite(n)) return 0.5;
    return Math.min(0.85, Math.max(0.15, n));
  } catch {
    return 0.5;
  }
}

function readCollapsed(): CollapsedState {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY);
    if (!raw) return { chats: false, files: false };
    const parsed = JSON.parse(raw) as Partial<CollapsedState>;
    return { chats: !!parsed.chats, files: !!parsed.files };
  } catch {
    return { chats: false, files: false };
  }
}

export function useSidebarPanelLayout() {
  const [order, setOrder] = useState<SidebarPanelId[]>(readOrder);
  const [splitRatio, setSplitRatio] = useState(readSplit);
  const [collapsed, setCollapsed] = useState<CollapsedState>(readCollapsed);
  const [focusedPanel, setFocusedPanel] = useState<SidebarPanelId>("chats");
  const splitRef = useRef(splitRatio);
  splitRef.current = splitRatio;

  const persistOrder = useCallback((next: SidebarPanelId[]) => {
    try {
      localStorage.setItem(ORDER_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  }, []);

  const persistSplit = useCallback(() => {
    try {
      localStorage.setItem(SPLIT_KEY, String(splitRef.current));
    } catch {
      // ignore
    }
  }, []);

  const persistCollapsed = useCallback((next: CollapsedState) => {
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  }, []);

  const toggleCollapsed = useCallback(
    (id: SidebarPanelId) => {
      setCollapsed((prev) => {
        const next = { ...prev, [id]: !prev[id] };
        persistCollapsed(next);
        return next;
      });
    },
    [persistCollapsed],
  );

  const swapOrder = useCallback(() => {
    setOrder((prev) => {
      const next: SidebarPanelId[] = [prev[1]!, prev[0]!];
      persistOrder(next);
      return next;
    });
  }, [persistOrder]);

  const swapPanels = useCallback(
    (panelA: SidebarPanelId, panelB: SidebarPanelId, edge?: StackedPanelDropEdge) => {
      setOrder((prev) => {
        const next = reorderStackedPanels(prev, panelA, panelB, edge);
        persistOrder(next);
        return next;
      });
    },
    [persistOrder],
  );

  const resizeSplit = useCallback(
    (splitIndex: number, deltaPx: number, containerHeight: number, snapshot?: StackedPanelResizeSnapshot<SidebarPanelId>) => {
      if (containerHeight <= 0) return;
      const next = resizeStackedPanelSplit(
        { order, collapsed, splitRatio: splitRef.current, minPanelHeight: MIN_SIDEBAR_PANEL_HEIGHT },
        splitIndex, deltaPx, containerHeight, snapshot,
      ).splitRatio;
      splitRef.current = next;
      setSplitRatio(next);
    },
    [order, collapsed],
  );

  return {
    order,
    splitRatio,
    collapsed,
    focusedPanel,
    setFocusedPanel,
    toggleCollapsed,
    swapOrder,
    swapPanels,
    resizeSplit,
    persistSplit,
  };
}

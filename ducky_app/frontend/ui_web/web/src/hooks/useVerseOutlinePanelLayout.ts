import { useCallback, useRef, useState } from "react";

export type VerseOutlinePanelId = "outline" | "history";

export const MIN_VERSE_OUTLINE_PANEL_HEIGHT = 80;

const ORDER_KEY = "uefn-verse-outline-panel-order";
const SPLIT_KEY = "uefn-verse-outline-panel-split";
const COLLAPSED_KEY = "uefn-verse-outline-panel-collapsed";

const DEFAULT_ORDER: VerseOutlinePanelId[] = ["outline", "history"];

type CollapsedState = Record<VerseOutlinePanelId, boolean>;

function readOrder(): VerseOutlinePanelId[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY);
    if (!raw) return DEFAULT_ORDER;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed) || parsed.length !== 2) return DEFAULT_ORDER;
    if (parsed.includes("outline") && parsed.includes("history")) return parsed as VerseOutlinePanelId[];
  } catch {
    // ignore
  }
  return DEFAULT_ORDER;
}

function readSplit(): number {
  try {
    const raw = localStorage.getItem(SPLIT_KEY);
    if (raw === null) return 0.55;
    const n = Number(raw);
    if (!Number.isFinite(n)) return 0.55;
    return Math.min(0.85, Math.max(0.15, n));
  } catch {
    return 0.55;
  }
}

function readCollapsed(): CollapsedState {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY);
    if (!raw) return { outline: false, history: false };
    const parsed = JSON.parse(raw) as Partial<CollapsedState>;
    return { outline: !!parsed.outline, history: !!parsed.history };
  } catch {
    return { outline: false, history: false };
  }
}

export function useVerseOutlinePanelLayout() {
  const [order, setOrder] = useState<VerseOutlinePanelId[]>(readOrder);
  const [splitRatio, setSplitRatio] = useState(readSplit);
  const [collapsed, setCollapsed] = useState<CollapsedState>(readCollapsed);
  const [focusedPanel, setFocusedPanel] = useState<VerseOutlinePanelId>("outline");
  const splitRef = useRef(splitRatio);
  splitRef.current = splitRatio;

  const persistOrder = useCallback((next: VerseOutlinePanelId[]) => {
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
    (id: VerseOutlinePanelId) => {
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
      const next: VerseOutlinePanelId[] = [prev[1]!, prev[0]!];
      persistOrder(next);
      return next;
    });
  }, [persistOrder]);

  const swapPanels = useCallback(
    (panelA: VerseOutlinePanelId, panelB: VerseOutlinePanelId) => {
      setOrder((prev) => {
        const next = [...prev];
        const i = next.indexOf(panelA);
        const j = next.indexOf(panelB);
        if (i < 0 || j < 0) return prev;
        next[i] = panelB;
        next[j] = panelA;
        persistOrder(next);
        return next;
      });
    },
    [persistOrder],
  );

  const resizeSplit = useCallback(
    (splitIndex: number, deltaPx: number, containerHeight: number) => {
      if (containerHeight <= 0) return;
      const deltaRatio = deltaPx / containerHeight;
      setSplitRatio((prev) => {
        const minRatio = MIN_VERSE_OUTLINE_PANEL_HEIGHT / containerHeight;
        const maxRatio = 1 - minRatio;
        const next =
          splitIndex === 0
            ? Math.max(minRatio, Math.min(maxRatio, prev + deltaRatio))
            : Math.max(minRatio, Math.min(maxRatio, prev - deltaRatio));
        splitRef.current = next;
        return next;
      });
    },
    [],
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

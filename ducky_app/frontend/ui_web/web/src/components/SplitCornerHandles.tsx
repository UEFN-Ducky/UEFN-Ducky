import { Fragment, useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

import type { EditorLayoutState, SplitAxis } from "../types/panel";

export interface CornerResizeOp {
  splitId: string;
  childIndex: number;
  axis: SplitAxis;
  deltaPx: number;
}

interface CornerTarget {
  splitId: string;
  childIndex: number;
  axis: SplitAxis;
}

interface Corner {
  key: string;
  x: number;
  y: number;
  targets: CornerTarget[];
}

/** How far (px) a divider's hit area extends past its 1px line — matches the ::before padding. */
const HIT_RADIUS = 6;
/** Bucket size for merging near-coincident crossings into one corner. */
const MERGE_PX = 8;

interface HandleInfo extends CornerTarget {
  rect: DOMRect;
}

function within(rect: DOMRect, x: number, y: number): boolean {
  return (
    x >= rect.left - HIT_RADIUS &&
    x <= rect.right + HIT_RADIUS &&
    y >= rect.top - HIT_RADIUS &&
    y <= rect.bottom + HIT_RADIUS
  );
}

interface SplitCornerHandlesProps {
  containerRef: RefObject<HTMLDivElement | null>;
  layout: EditorLayoutState;
  onResizeMany: (ops: CornerResizeOp[]) => void;
}

/**
 * Overlays a grabber at every point where a vertical and horizontal split divider
 * meet (4-pane crossings and 3-pane T-junctions). Dragging it resizes every
 * adjacent divider at once, VS Code style, with a 4-way cursor.
 */
export function SplitCornerHandles({ containerRef, layout, onResizeMany }: SplitCornerHandlesProps) {
  const scopeClass = useScopedClass("split-corner-handle");
  const [corners, setCorners] = useState<Corner[]>([]);
  const onResizeManyRef = useRef(onResizeMany);
  onResizeManyRef.current = onResizeMany;

  const getHandleEls = useCallback(
    (targets: CornerTarget[]): HTMLElement[] => {
      const container = containerRef.current;
      if (!container) return [];
      return targets
        .map((t) =>
          container.querySelector<HTMLElement>(
            `.split-resize-handle[data-split-id="${t.splitId}"][data-child-index="${t.childIndex}"]`,
          ),
        )
        .filter((el): el is HTMLElement => !!el);
    },
    [containerRef],
  );

  const recompute = useCallback(() => {
    const container = containerRef.current;
    if (!container) {
      setCorners([]);
      return;
    }
    const rootRect = container.getBoundingClientRect();
    const handles: HandleInfo[] = Array.from(
      container.querySelectorAll<HTMLElement>(".split-resize-handle[data-split-id]"),
    ).map((el) => ({
      splitId: el.dataset.splitId ?? "",
      childIndex: Number(el.dataset.childIndex ?? -1),
      axis: (el.dataset.splitAxis === "column" ? "column" : "row") as SplitAxis,
      rect: el.getBoundingClientRect(),
    }));
    // "row" splits lay children side by side, so their dividers are vertical lines.
    const verticals = handles.filter((h) => h.axis === "row");
    const horizontals = handles.filter((h) => h.axis === "column");
    if (verticals.length === 0 || horizontals.length === 0) {
      setCorners([]);
      return;
    }
    const found = new Map<string, Corner>();
    for (const v of verticals) {
      const cx = v.rect.left + v.rect.width / 2;
      for (const hz of horizontals) {
        const cy = hz.rect.top + hz.rect.height / 2;
        if (!within(v.rect, cx, cy) || !within(hz.rect, cx, cy)) continue;
        const key = `${Math.round(cx / MERGE_PX)}:${Math.round(cy / MERGE_PX)}`;
        if (found.has(key)) continue;
        const targets = handles
          .filter((h) => within(h.rect, cx, cy))
          .map(({ splitId, childIndex, axis }) => ({ splitId, childIndex, axis }));
        found.set(key, { key, x: cx - rootRect.left, y: cy - rootRect.top, targets });
      }
    }
    setCorners([...found.values()]);
  }, [containerRef]);

  useLayoutEffect(() => {
    recompute();
  }, [recompute, layout]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => recompute());
    observer.observe(container);
    return () => observer.disconnect();
  }, [containerRef, recompute]);

  const setCornerHover = useCallback(
    (corner: Corner, on: boolean) => {
      for (const el of getHandleEls(corner.targets)) el.classList.toggle("is-corner-hover", on);
    },
    [getHandleEls],
  );

  const startDrag = (corner: Corner) => (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();

    const targets = corner.targets;
    const highlighted = getHandleEls(targets);
    for (const el of highlighted) el.classList.add("is-corner-hover");
    document.body.style.cursor = "all-scroll";

    let lastX = e.clientX;
    let lastY = e.clientY;

    const onPointerMove = (ev: PointerEvent) => {
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      lastX = ev.clientX;
      lastY = ev.clientY;
      const ops = targets
        .map((t) => ({ ...t, deltaPx: t.axis === "row" ? dx : dy }))
        .filter((op) => op.deltaPx !== 0);
      if (ops.length) onResizeManyRef.current(ops);
    };

    const endDrag = () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      document.body.style.cursor = "";
      for (const el of highlighted) el.classList.remove("is-corner-hover");
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  };

  return (
    <>
      {corners.map((corner) => (
        <Fragment key={corner.key}>
          <ScopedCss
            selector={`.${scopeClass}[data-corner="${corner.key}"]`}
            rules={{ left: `${corner.x}px`, top: `${corner.y}px` }}
          />
          <div
            className={`split-corner-handle ${scopeClass} no-drag`}
            data-corner={corner.key}
            onPointerEnter={() => setCornerHover(corner, true)}
            onPointerLeave={() => setCornerHover(corner, false)}
            onPointerDown={startDrag(corner)}
            role="separator"
            aria-label="Resize panes in both directions"
          />
        </Fragment>
      ))}
    </>
  );
}

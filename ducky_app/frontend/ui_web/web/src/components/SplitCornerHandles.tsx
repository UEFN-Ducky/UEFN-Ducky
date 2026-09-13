import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

import { frameBatch } from "../utils/frameBatch";
import { pointerResize } from "../utils/pointerResize";

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
  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanupRef.current?.(), []);
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
      setCorners((prev) => prev.length ? [] : prev);
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
      setCorners((prev) => prev.length ? [] : prev);
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
    const next = [...found.values()];
    setCorners((prev) => {
      const unchanged = prev.length === next.length && prev.every((corner, i) => {
        const other = next[i]!;
        return corner.key === other.key && corner.x === other.x && corner.y === other.y
          && corner.targets.length === other.targets.length
          && corner.targets.every((target, j) => {
            const otherTarget = other.targets[j]!;
            return target.splitId === otherTarget.splitId
              && target.childIndex === otherTarget.childIndex && target.axis === otherTarget.axis;
          });
      });
      return unchanged ? prev : next;
    });
  }, [containerRef]);

  useLayoutEffect(() => {
    recompute();
  }, [recompute, layout.root]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const batch = frameBatch(recompute);
    const observer = new ResizeObserver(batch.schedule);
    observer.observe(container);
    return () => { observer.disconnect(); batch.cancel(); };
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

    cleanupRef.current?.();
    const targets = corner.targets;
    const highlighted = getHandleEls(targets);
    for (const el of highlighted) el.classList.add("is-corner-hover");
    const previousCursor = document.body.style.cursor;
    document.body.style.cursor = "all-scroll";

    const reset = () => {
      document.body.style.cursor = previousCursor;
      for (const el of highlighted) el.classList.remove("is-corner-hover");
    };
    const stop = pointerResize(e, (dx, dy) => {
      const ops = targets
        .map((t) => ({ ...t, deltaPx: t.axis === "row" ? dx : dy }))
        .filter((op) => op.deltaPx !== 0);
      if (ops.length) onResizeManyRef.current(ops);
    }, () => { reset(); cleanupRef.current = null; });
    cleanupRef.current = () => { stop(); reset(); };
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

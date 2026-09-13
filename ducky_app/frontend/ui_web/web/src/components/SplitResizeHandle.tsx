import { useEffect, useRef, useState } from "react";
import { pointerResize } from "../utils/pointerResize";

interface SplitResizeHandleProps {
  onDrag: (delta: number) => void;
  onDragEnd?: () => void;
  orientation?: "horizontal" | "vertical";
  ariaLabel?: string;
  className?: string;
  /** Identify the split-tree node this handle controls so corner grabbers can find it. */
  splitId?: string;
  childIndex?: number;
  splitAxis?: "row" | "column";
}

export function SplitResizeHandle({
  onDrag,
  onDragEnd,
  orientation = "horizontal",
  ariaLabel = "Resize split panes",
  className = "",
  splitId,
  childIndex,
  splitAxis,
}: SplitResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanupRef.current?.(), []);
  const onDragRef = useRef(onDrag);
  const onDragEndRef = useRef(onDragEnd);
  onDragRef.current = onDrag;
  onDragEndRef.current = onDragEnd;

  const isVertical = orientation === "vertical";

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    cleanupRef.current?.();
    setDragging(true);
    cleanupRef.current = pointerResize(e, (dx, dy) => {
      const delta = isVertical ? dy : dx;
      if (delta) onDragRef.current(delta);
    }, () => {
      setDragging(false);
      onDragEndRef.current?.();
      cleanupRef.current = null;
    });
  };

  return (
    <div
      className={`split-resize-handle no-drag${dragging ? " is-dragging" : ""}${isVertical ? " split-resize-handle--horizontal" : ""}${className ? ` ${className}` : ""}`}
      data-split-id={splitId}
      data-child-index={childIndex}
      data-split-axis={splitAxis}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation={isVertical ? "horizontal" : "vertical"}
      aria-label={ariaLabel}
    />
  );
}

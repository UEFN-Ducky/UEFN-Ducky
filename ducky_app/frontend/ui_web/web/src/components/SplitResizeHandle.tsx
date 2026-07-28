import { useRef, useState } from "react";

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
  const lastPos = useRef(0);
  const onDragRef = useRef(onDrag);
  const onDragEndRef = useRef(onDragEnd);
  onDragRef.current = onDrag;
  onDragEndRef.current = onDragEnd;

  const isVertical = orientation === "vertical";

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();

    const onPointerMove = (ev: PointerEvent) => {
      const pos = isVertical ? ev.clientY : ev.clientX;
      const delta = pos - lastPos.current;
      lastPos.current = pos;
      if (delta !== 0) onDragRef.current(delta);
    };

    const endDrag = () => {
      setDragging(false);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      onDragEndRef.current?.();
    };

    lastPos.current = isVertical ? e.clientY : e.clientX;
    setDragging(true);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
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

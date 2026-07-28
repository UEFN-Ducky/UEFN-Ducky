import { useRef, useState } from "react";

interface ChatColumnResizeHandleProps {
  side: "left" | "right";
  onDrag: (deltaX: number) => void;
  onDragEnd?: () => void;
  /** Override default "Resize chat column" for other split panes. */
  label?: string;
}

export function ChatColumnResizeHandle({
  side,
  onDrag,
  onDragEnd,
  label = "Resize chat column",
}: ChatColumnResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const lastX = useRef(0);
  const onDragRef = useRef(onDrag);
  const onDragEndRef = useRef(onDragEnd);
  onDragRef.current = onDrag;
  onDragEndRef.current = onDragEnd;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();

    const onPointerMove = (ev: PointerEvent) => {
      const delta = ev.clientX - lastX.current;
      lastX.current = ev.clientX;
      if (delta !== 0) onDragRef.current(delta);
    };

    const endDrag = () => {
      setDragging(false);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      onDragEndRef.current?.();
    };

    lastX.current = e.clientX;
    setDragging(true);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  };

  return (
    <div
      className={`chat-column-resize-handle no-drag chat-column-resize-handle--${side}${dragging ? " is-dragging" : ""}`}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
    />
  );
}

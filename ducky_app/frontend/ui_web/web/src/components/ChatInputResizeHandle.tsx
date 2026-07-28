import { useRef, useState } from "react";

const DRAG_THRESHOLD_PX = 4;

interface ChatInputResizeHandleProps {
  onDrag: (deltaY: number) => void;
  onTap?: () => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
}

export function ChatInputResizeHandle({ onDrag, onTap, onDragStart, onDragEnd }: ChatInputResizeHandleProps) {
  const [dragging, setDragging] = useState(false);
  const startY = useRef(0);
  const lastY = useRef(0);
  const isDrag = useRef(false);
  const onDragRef = useRef(onDrag);
  const onTapRef = useRef(onTap);
  const onDragStartRef = useRef(onDragStart);
  const onDragEndRef = useRef(onDragEnd);
  onDragRef.current = onDrag;
  onTapRef.current = onTap;
  onDragStartRef.current = onDragStart;
  onDragEndRef.current = onDragEnd;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const handleEl = e.currentTarget;
    handleEl.setPointerCapture(e.pointerId);

    const onPointerMove = (ev: PointerEvent) => {
      if (!isDrag.current && Math.abs(ev.clientY - startY.current) >= DRAG_THRESHOLD_PX) {
        isDrag.current = true;
        setDragging(true);
        onDragStartRef.current?.();
      }
      if (!isDrag.current) return;

      const delta = lastY.current - ev.clientY;
      lastY.current = ev.clientY;
      if (delta !== 0) onDragRef.current(delta);
    };

    const endDrag = (ev: PointerEvent) => {
      setDragging(false);
      if (handleEl.hasPointerCapture(ev.pointerId)) {
        handleEl.releasePointerCapture(ev.pointerId);
      }
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      if (!isDrag.current) {
        onTapRef.current?.();
      } else {
        onDragEndRef.current?.();
      }
    };

    startY.current = e.clientY;
    lastY.current = e.clientY;
    isDrag.current = false;
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  };

  return (
    <div
      className={`chat-pane-input-resize-handle no-drag${dragging ? " is-dragging" : ""}`}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize chat input"
    />
  );
}

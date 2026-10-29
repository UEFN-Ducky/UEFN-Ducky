import { useCallback, useEffect, useRef, useState, type CSSProperties, type HTMLAttributes, type PointerEvent, type SyntheticEvent } from "react";

const HOLD_MS = 450;
const MOVE_PX = 10;

/**
 * Touch long-press on a tree row: hold still for 450 ms and `onLongPress` runs (with a
 * short buzz). The click, context menu and drag that a hold would otherwise start are
 * swallowed. Mouse and pen are untouched.
 */
export function useLongPressSelect(onLongPress: () => void): HTMLAttributes<HTMLDivElement> {
  const timer = useRef<number | null>(null);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const fired = useRef(false);
  const [pressing, setPressing] = useState(false);
  const callback = useRef(onLongPress);
  callback.current = onLongPress;

  const clear = useCallback(() => {
    if (timer.current != null) window.clearTimeout(timer.current);
    timer.current = null;
    origin.current = null;
    setPressing(false);
  }, []);

  const fire = useCallback(() => {
    clear();
    fired.current = true;
    navigator.vibrate?.(15);
    callback.current();
  }, [clear]);

  useEffect(() => clear, [clear]);

  /** Swallow what a hold would trigger; a hold still in progress fires now (the OS menu came first). */
  const swallow = useCallback(
    (e: SyntheticEvent) => {
      if (timer.current != null) fire();
      else if (!fired.current) return;
      e.preventDefault();
      e.stopPropagation();
    },
    [fire],
  );

  const style: CSSProperties = pressing
    ? { touchAction: "pan-y", WebkitTouchCallout: "none", userSelect: "none", WebkitUserSelect: "none" }
    : { touchAction: "pan-y" };

  return {
    style,
    onPointerDown: (e: PointerEvent) => {
      fired.current = false;
      if (e.pointerType !== "touch") return;
      clear();
      origin.current = { x: e.clientX, y: e.clientY };
      setPressing(true);
      timer.current = window.setTimeout(fire, HOLD_MS);
    },
    onPointerMove: (e: PointerEvent) => {
      const start = origin.current;
      if (!start || timer.current == null) return;
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > MOVE_PX) clear();
    },
    onPointerUp: clear,
    onPointerCancel: clear,
    onClickCapture: (e) => {
      if (!fired.current) return;
      fired.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
    onContextMenuCapture: swallow,
    onDragStartCapture: swallow,
  };
}

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { swipeShouldOpen, swipeSideFromPoint, swipeTranslateX } from "../utils/railEdgeSwipe";

const LOCK_PX = 12;

type Drag = {
  side: "left" | "right";
  tx: number;
};

export function useRailEdgeSwipe(opts: {
  enabled: boolean;
  leftOpen: boolean;
  rightOpen: boolean;
  leftEnabled: boolean;
  rightEnabled: boolean;
  leftWidth: number;
  rightWidth: number;
  setLeftOpen: (open: boolean) => void;
  setRightOpen: (open: boolean) => void;
}) {
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const [drag, setDrag] = useState<Drag | null>(null);
  const session = useRef<{
    pointerId: number;
    side: "left" | "right";
    open: boolean;
    startX: number;
    startY: number;
    lastX: number;
    lastT: number;
    vx: number;
    locked: boolean;
    width: number;
  } | null>(null);

  const endDrag = useCallback((commit: boolean) => {
    const s = session.current;
    session.current = null;
    if (!s) {
      setDrag(null);
      return;
    }
    const o = optsRef.current;
    if (commit && s.locked) {
      const tx = swipeTranslateX(s.side, s.open, s.lastX - s.startX, s.width);
      const open = swipeShouldOpen(s.side, tx, s.width, s.vx);
      if (s.side === "left") {
        o.setLeftOpen(open);
        if (open) o.setRightOpen(false);
      } else {
        o.setRightOpen(open);
        if (open) o.setLeftOpen(false);
      }
    }
    setDrag(null);
  }, []);

  useEffect(() => {
    if (!opts.enabled) endDrag(false);
  }, [opts.enabled, endDrag]);

  const onPointerDownCapture = useCallback((e: ReactPointerEvent<HTMLElement>) => {
    const o = optsRef.current;
    if (!o.enabled || e.button !== 0) return;
    const target = e.target as HTMLElement | null;
    if (target?.closest?.(".dock-rail-resize-handle, input, textarea, [contenteditable='true']")) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const side = swipeSideFromPoint(
      e.clientX,
      rect.left,
      rect.right,
      o.leftOpen,
      o.rightOpen,
      o.leftEnabled,
      o.rightEnabled,
      o.leftWidth,
      o.rightWidth,
    );
    if (!side) return;
    session.current = {
      pointerId: e.pointerId,
      side,
      open: side === "left" ? o.leftOpen : o.rightOpen,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      lastT: e.timeStamp,
      vx: 0,
      locked: false,
      width: side === "left" ? o.leftWidth : o.rightWidth,
    };
  }, []);

  const onPointerMoveCapture = useCallback((e: ReactPointerEvent<HTMLElement>) => {
    const s = session.current;
    if (!s || e.pointerId !== s.pointerId) return;
    const dx = e.clientX - s.startX;
    const dy = e.clientY - s.startY;
    if (!s.locked) {
      if (Math.abs(dx) < LOCK_PX && Math.abs(dy) < LOCK_PX) return;
      if (Math.abs(dy) >= Math.abs(dx)) {
        session.current = null;
        return;
      }
      s.locked = true;
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    const dt = Math.max(1, e.timeStamp - s.lastT);
    s.vx = (e.clientX - s.lastX) / dt;
    s.lastX = e.clientX;
    s.lastT = e.timeStamp;
    setDrag({ side: s.side, tx: swipeTranslateX(s.side, s.open, dx, s.width) });
    e.preventDefault();
  }, []);

  const onPointerUpCapture = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      const s = session.current;
      if (!s || e.pointerId !== s.pointerId) return;
      if (s.locked) {
        try {
          e.currentTarget.releasePointerCapture(e.pointerId);
        } catch {
          /* already released */
        }
      }
      endDrag(s.locked);
    },
    [endDrag],
  );

  return { drag, onPointerDownCapture, onPointerMoveCapture, onPointerUpCapture };
}

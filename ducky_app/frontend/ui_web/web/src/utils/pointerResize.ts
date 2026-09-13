import { frameBatch } from "./frameBatch";

/** Track one pointer, accumulating movement until the next paint. Returns teardown. */
export function pointerResize(
  start: { pointerId: number; clientX: number; clientY: number },
  move: (dx: number, dy: number) => void,
  end: () => void,
): () => void {
  let x = start.clientX;
  let y = start.clientY;
  let nextX = x;
  let nextY = y;
  let stopped = false;
  const batch = frameBatch(() => {
    const dx = nextX - x;
    const dy = nextY - y;
    x = nextX;
    y = nextY;
    if (dx || dy) move(dx, dy);
  });
  const onMove = (event: PointerEvent) => {
    if (event.pointerId !== start.pointerId) return;
    nextX = event.clientX;
    nextY = event.clientY;
    batch.schedule();
  };
  const cleanup = () => {
    stopped = true;
    batch.cancel();
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onEnd);
    window.removeEventListener("pointercancel", onEnd);
    window.removeEventListener("blur", finish);
  };
  const finish = () => {
    if (stopped) return;
    // Persist only after the last queued movement has been applied.
    batch.flush();
    cleanup();
    end();
  };
  const onEnd = (event: PointerEvent) => {
    if (event.pointerId !== start.pointerId) return;
    if (event.type === "pointerup") onMove(event);
    finish();
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onEnd);
  window.addEventListener("pointercancel", onEnd);
  window.addEventListener("blur", finish);
  return cleanup;
}

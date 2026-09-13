/** Coalesce bursts into one update per paint without postponing an already queued frame. */
export function frameBatch(callback: () => void) {
  let frame: number | null = null;
  const cancel = () => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  };
  return {
    schedule() {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        callback();
      });
    },
    flush() {
      if (frame === null) return;
      cancel();
      callback();
    },
    cancel,
  };
}

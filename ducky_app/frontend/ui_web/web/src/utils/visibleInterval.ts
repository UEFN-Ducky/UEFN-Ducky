/**
 * A polling interval that stops while the window is hidden.
 *
 * Every poll in the panel used to run forever regardless of whether anyone
 * could see the result, which cost about 12% of a core at idle — on battery,
 * behind another window, minimised to the tray. This stops the timer when the
 * window is hidden and fires once on the way back, so what you see on return
 * is fresh rather than up to one interval stale.
 *
 * Returns the canceller, so a `useEffect` can `return setVisibleInterval(...)`
 * and drop its own `clearInterval` bookkeeping.
 */
export function setVisibleInterval(fn: () => void, ms: number): () => void {
  if (typeof document === "undefined") {
    const id = setInterval(fn, ms);
    return () => clearInterval(id);
  }
  let id = 0;
  const start = () => {
    if (!id) id = window.setInterval(fn, ms);
  };
  const stop = () => {
    if (id) {
      window.clearInterval(id);
      id = 0;
    }
  };
  const onVisibility = () => {
    if (document.hidden) {
      stop();
      return;
    }
    // Catch up before resuming: the window may have been hidden for hours.
    fn();
    start();
  };
  document.addEventListener("visibilitychange", onVisibility);
  if (!document.hidden) start();
  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    stop();
  };
}

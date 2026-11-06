/** Dismiss the inline `#boot-splash` from index.html once the React shell is drawn. */

const SPLASH_ID = "boot-splash";
/** Matches the `transition: opacity 0.05s` in index.html. */
export const FADE_MS = 50;
/** A root that never fills (a crash before ErrorBoundary) must not keep the splash up forever. */
const MAX_WAIT_MS = 5000;

/** No fade: reduced motion, or the native launcher (its first data came from the start-up
 *  cache, so the UI underneath is complete the moment it commits). */
function instant(): boolean {
  if ((window as Window & { pywebview?: { native?: boolean } }).pywebview?.native) return true;
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function dismissBootSplash(): void {
  const el = document.getElementById(SPLASH_ID);
  if (!el || el.classList.contains("boot-splash--done") || instant()) {
    el?.remove();
    return;
  }
  el.classList.add("boot-splash--done");
  el.setAttribute("aria-busy", "false");
  window.setTimeout(() => {
    el.remove();
  }, FADE_MS);
}

function rootHasContent(): boolean {
  const root = document.getElementById("root");
  return !root || root.childElementCount > 0;
}

/**
 * Hide the splash on the first frame after React has committed the shell into `#root`
 * (never while the root is still empty, so no blank flash). The desktop showed the logo
 * on its native splash already; holding this one longer only hides a drawn app.
 */
export function dismissBootSplashAfterPaint(): void {
  const start = performance.now();
  const tick = () => {
    if (rootHasContent() || performance.now() - start > MAX_WAIT_MS) dismissBootSplash();
    else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

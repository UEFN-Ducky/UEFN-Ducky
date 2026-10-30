/** Dismiss the inline `#boot-splash` from index.html after the React shell has painted. */

const SPLASH_ID = "boot-splash";
const FADE_MS = 220;
/** Minimum time the in-window logo splash stays up once HTML has painted (desktop). */
const MIN_HOLD_MS = 1200;

export function dismissBootSplash(): void {
  const el = document.getElementById(SPLASH_ID);
  if (!el || el.classList.contains("boot-splash--done")) {
    el?.remove();
    return;
  }
  el.classList.add("boot-splash--done");
  el.setAttribute("aria-busy", "false");
  window.setTimeout(() => {
    el.remove();
  }, FADE_MS);
}

/** The phone panel: the site's Remote View already shows its own loading screen. */
function phonePanel(): boolean {
  return new URLSearchParams(window.location.search).has("direct");
}

/**
 * Wait until the React shell has painted *and* (desktop only) the splash has been
 * visible for at least {@link MIN_HOLD_MS} since navigation, then fade it out.
 */
export function dismissBootSplashAfterPaint(): void {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const remaining = phonePanel() ? 0 : Math.max(0, MIN_HOLD_MS - performance.now());
      window.setTimeout(() => dismissBootSplash(), remaining);
    });
  });
}

/** Dismiss the inline `#boot-splash` from index.html after the React shell has painted. */

const SPLASH_ID = "boot-splash";
const FADE_MS = 160;

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

/**
 * Fade the splash out as soon as the React shell has painted. The desktop showed the logo
 * on its native splash since ~0.6 s already; a 1.2 s minimum hold here kept a drawn app
 * hidden on every start.
 */
export function dismissBootSplashAfterPaint(): void {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => dismissBootSplash());
  });
}

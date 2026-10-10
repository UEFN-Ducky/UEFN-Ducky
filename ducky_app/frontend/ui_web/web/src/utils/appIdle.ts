/**
 * Whether the window has been left in the background.
 *
 * Ducky sits next to the UEFN editor all day. An endless glow, a pulse travelling a
 * workflow wire or the Matrix rain repaints every frame even when nobody uses the window,
 * so those pause on their current frame once the window has been out of focus for a while
 * (or is hidden), and carry on as soon as it is focused, shown or pointed at again.
 * While idle the root element carries the `app-idle` class for stylesheets to use.
 */

const IDLE_AFTER_MS = 30_000;
export const APP_IDLE_CLASS = "app-idle";

const listeners = new Set<(idle: boolean) => void>();
let idle = false;
let timer = 0;
let uninstall: (() => void) | null = null;

function setIdle(next: boolean): void {
  if (idle === next) return;
  idle = next;
  document.documentElement.classList.toggle(APP_IDLE_CLASS, next);
  for (const listener of listeners) listener(next);
}

function update(): void {
  window.clearTimeout(timer);
  timer = 0;
  if (document.hidden) {
    setIdle(true);
    return;
  }
  setIdle(false);
  // Focus inside a plugin's iframe still counts as using the window.
  if (!document.hasFocus()) {
    timer = window.setTimeout(() => {
      timer = 0;
      if (document.hidden || !document.hasFocus()) setIdle(true);
    }, IDLE_AFTER_MS);
  }
}

function wake(): void {
  if (idle) update();
}

/** Starts tracking (once); the returned function stops it again. */
export function installAppIdle(): () => void {
  if (uninstall || typeof document === "undefined") return uninstall ?? (() => {});
  window.addEventListener("focus", update);
  window.addEventListener("blur", update);
  window.addEventListener("pointermove", wake, { passive: true });
  document.addEventListener("visibilitychange", update);
  update();
  uninstall = () => {
    window.removeEventListener("focus", update);
    window.removeEventListener("blur", update);
    window.removeEventListener("pointermove", wake);
    document.removeEventListener("visibilitychange", update);
    window.clearTimeout(timer);
    timer = 0;
    uninstall = null;
    setIdle(false);
  };
  return uninstall;
}

export function isAppIdle(): boolean {
  return idle;
}

/** Calls `listener` whenever the window goes idle or comes back. */
export function subscribeAppIdle(listener: (idle: boolean) => void): () => void {
  installAppIdle();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

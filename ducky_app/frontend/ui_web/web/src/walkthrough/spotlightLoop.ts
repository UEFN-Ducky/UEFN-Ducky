/**
 * Keeps the tour spotlight on its target without a frame loop.
 *
 * The overlay used to measure the target and rewrite its CSS variables on every animation
 * frame for as long as a tour was open: a pending requestAnimationFrame makes Chromium
 * run a frame each vsync, and each rewrite recalculated style. With 10 agents working that
 * was ~100 style recalcs a second and a third of a core in WebView2, while nothing moved.
 *
 * Now it measures every `idleMs`, applies only what changed, and follows frame by frame
 * for `burstFrames` frames after a change (the target is moving: an opening menu, a
 * scroll) or after a resize or scroll, so a moving target still tracks smoothly.
 */

export interface SpotlightLoopOptions {
  idleMs?: number;
  burstFrames?: number;
}

export interface SpotlightLoopDeps {
  requestFrame: (cb: () => void) => number;
  cancelFrame: (id: number) => void;
  setTimer: (cb: () => void, ms: number) => number;
  clearTimer: (id: number) => void;
}

const browserDeps = (): SpotlightLoopDeps => ({
  requestFrame: (cb) => window.requestAnimationFrame(() => cb()),
  cancelFrame: (id) => window.cancelAnimationFrame(id),
  setTimer: (cb, ms) => window.setTimeout(cb, ms),
  clearTimer: (id) => window.clearTimeout(id),
});

/**
 * `measure` returns a key for what the spotlight should show now (all values it would
 * write, joined); `apply` writes it and runs only when the key changed. Returns
 * `{ wake, stop }`: `wake` follows frame by frame again (a resize, a scroll).
 */
export function startSpotlightLoop(
  measure: () => string,
  apply: (key: string) => void,
  { idleMs = 250, burstFrames = 12 }: SpotlightLoopOptions = {},
  deps: SpotlightLoopDeps = browserDeps(),
): { wake: () => void; stop: () => void } {
  let last: string | null = null;
  let burst = 0;
  let frame = 0;
  let timer = 0;
  let stopped = false;

  const clear = () => {
    if (frame) deps.cancelFrame(frame);
    if (timer) deps.clearTimer(timer);
    frame = 0;
    timer = 0;
  };

  const schedule = () => {
    if (stopped) return;
    if (burst > 0) frame = deps.requestFrame(tick);
    else timer = deps.setTimer(tick, idleMs);
  };

  function tick() {
    frame = 0;
    timer = 0;
    if (stopped) return;
    const key = measure();
    if (key !== last) {
      last = key;
      apply(key);
      burst = burstFrames;
    } else if (burst > 0) {
      burst -= 1;
    }
    schedule();
  }

  tick();
  return {
    wake: () => {
      if (stopped) return;
      clear();
      burst = Math.max(burst, burstFrames);
      frame = deps.requestFrame(tick);
    },
    stop: () => {
      stopped = true;
      clear();
    },
  };
}

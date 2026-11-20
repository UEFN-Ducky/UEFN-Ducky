/**
 * One clock for every running time in the window.
 *
 * Each running tool card, workflow step, chat timer and "5m ago" label kept its own
 * setInterval (13 places, Nov 20 2026), one per thing on screen: a team of agents with a
 * few tools each meant dozens of timers a second. Each woke React on its own, so the window
 * drew them in separate passes, and all of them went on ticking while the window was hidden.
 *
 * Here everything that asks for the same period shares one timer. Its listeners hear the
 * same `now` in one task, so React draws them in one pass, and the timer stops while the
 * window is hidden (utils/visibleInterval: it fires once on the way back).
 */

import { useEffect, useState } from "react";
import { setVisibleInterval } from "./visibleInterval";

type Listener = (now: number) => void;
type Beat = { listeners: Set<Listener>; stop: () => void };

const beats = new Map<number, Beat>();

/** Calls `listener(now)` every `ms` while the window is shown. Returns the canceller. */
export function subscribeClock(ms: number, listener: Listener): () => void {
  let beat = beats.get(ms);
  if (!beat) {
    const listeners = new Set<Listener>();
    const stop = setVisibleInterval(() => {
      const now = Date.now();
      for (const hear of [...listeners]) {
        try {
          hear(now);
        } catch {
          // one listener's error never stops the others' clocks
        }
      }
    }, ms);
    beat = { listeners, stop };
    beats.set(ms, beat);
  }
  const own = beat;
  // Its own function per subscription: the same callback passed twice is two listeners.
  const hear: Listener = (now) => listener(now);
  own.listeners.add(hear);
  return () => {
    own.listeners.delete(hear);
    if (!own.listeners.size && beats.get(ms) === own) {
      own.stop();
      beats.delete(ms);
    }
  };
}

/** How many timers run now (one per period in use), for tests and diagnostics. */
export function clockTimers(): number {
  return beats.size;
}

/**
 * The time, refreshed every `ms` while `on`. Turning it on reads the time at once; while
 * off it keeps the last value, so a finished run's label stays as it was.
 */
export function useNow(ms: number, on = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    // Fresh from the mount: no second render. Stale from an earlier run: read it now.
    setNow((old) => {
      const at = Date.now();
      return at - old < 50 ? old : at;
    });
    return subscribeClock(ms, setNow);
  }, [ms, on]);
  return now;
}

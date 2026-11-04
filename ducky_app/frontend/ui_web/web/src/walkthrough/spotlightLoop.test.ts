import { describe, expect, it } from "vitest";
import { startSpotlightLoop, type SpotlightLoopDeps } from "./spotlightLoop";

/** A clock that runs frames (16 ms) and timers in order, counting both. */
function fakeClock() {
  let now = 0;
  let seq = 0;
  const queue = new Map<number, { at: number; cb: () => void; frame: boolean }>();
  const counts = { frames: 0, timers: 0 };
  const deps: SpotlightLoopDeps = {
    requestFrame: (cb) => { queue.set(++seq, { at: now + 16, cb, frame: true }); return seq; },
    cancelFrame: (id) => { queue.delete(id); },
    setTimer: (cb, ms) => { queue.set(++seq, { at: now + ms, cb, frame: false }); return seq; },
    clearTimer: (id) => { queue.delete(id); },
  };
  const runFor = (ms: number) => {
    const end = now + ms;
    for (;;) {
      const next = [...queue.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > end) break;
      queue.delete(next[0]);
      now = next[1].at;
      if (next[1].frame) counts.frames += 1;
      else counts.timers += 1;
      next[1].cb();
    }
    now = end;
  };
  return { deps, runFor, counts, pending: () => queue.size };
}

describe("startSpotlightLoop", () => {
  it("a target that never moves is written once and costs no frame loop", () => {
    const clock = fakeClock();
    let measured = 0;
    const applied: string[] = [];
    startSpotlightLoop(() => { measured += 1; return "x=10|y=20"; }, (k) => applied.push(k),
      { idleMs: 250, burstFrames: 12 }, clock.deps);
    clock.runFor(60_000);
    expect(applied).toEqual(["x=10|y=20"]);
    // The old loop ran ~3,750 frames a minute; now: the settle burst, then 4 checks a second.
    expect(clock.counts.frames).toBeLessThanOrEqual(12);
    expect(measured).toBeLessThan(260);
  });

  it("a moving target is followed frame by frame, then it settles", () => {
    const clock = fakeClock();
    let x = 0;
    const applied: string[] = [];
    startSpotlightLoop(() => `x=${Math.min(x, 100)}`, (k) => applied.push(k), { idleMs: 250, burstFrames: 12 }, clock.deps);
    x = 10; // moves while the burst after the first write runs
    for (let i = 0; i < 9; i += 1) { clock.runFor(16); x += 10; }
    expect(applied.length).toBeGreaterThanOrEqual(9);
    expect(applied.at(-1)).toBe("x=90");
    const frames = clock.counts.frames;
    clock.runFor(10_000);
    expect(clock.counts.frames - frames).toBeLessThanOrEqual(13);
  });

  it("wake follows frame by frame again, stop cancels everything", () => {
    const clock = fakeClock();
    let key = "a";
    const applied: string[] = [];
    const loop = startSpotlightLoop(() => key, (k) => applied.push(k), { idleMs: 250, burstFrames: 4 }, clock.deps);
    clock.runFor(5_000);
    key = "b";
    loop.wake();
    clock.runFor(16);
    expect(applied).toEqual(["a", "b"]);
    loop.stop();
    expect(clock.pending()).toBe(0);
    key = "c";
    clock.runFor(5_000);
    expect(applied).toEqual(["a", "b"]);
  });
});

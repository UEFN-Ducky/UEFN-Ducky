import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { waitIceGathering } from "./protocol";

type Listener = (ev: unknown) => void;

function fakePc() {
  const on = new Map<string, Set<Listener>>();
  return {
    iceGatheringState: "gathering" as RTCIceGatheringState,
    addEventListener(type: string, fn: Listener) {
      if (!on.has(type)) on.set(type, new Set());
      on.get(type)!.add(fn);
    },
    removeEventListener(type: string, fn: Listener) {
      on.get(type)?.delete(fn);
    },
    candidate(typ: string) {
      for (const fn of on.get("icecandidate") ?? []) fn({ candidate: { candidate: `candidate:1 1 udp 1 1.2.3.4 5 typ ${typ}` } });
    },
    listeners: () => [...on.values()].reduce((n, s) => n + s.size, 0),
  };
}

async function settledAt(p: Promise<void>): Promise<number> {
  let at = -1;
  void p.then(() => (at = Date.now()));
  for (let t = 0; t <= 3000 && at < 0; t += 50) await vi.advanceTimersByTimeAsync(50);
  return at;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
});
afterEach(() => vi.useRealTimers());

describe("waitIceGathering", () => {
  it("sends shortly after the public address is known, not at the 2.5 s cap", async () => {
    const pc = fakePc();
    const p = waitIceGathering(pc as unknown as RTCPeerConnection, 2500);
    setTimeout(() => pc.candidate("host"), 50);
    setTimeout(() => pc.candidate("srflx"), 200);
    const at = await settledAt(p);
    expect(at).toBeGreaterThanOrEqual(500);
    expect(at).toBeLessThanOrEqual(550);
    expect(pc.listeners()).toBe(0);
  });

  it("waits out the cap when only host candidates come (no STUN answer)", async () => {
    const pc = fakePc();
    const p = waitIceGathering(pc as unknown as RTCPeerConnection, 2500);
    setTimeout(() => pc.candidate("host"), 50);
    expect(await settledAt(p)).toBe(2500);
  });

  it("returns at once when gathering already finished", async () => {
    const pc = fakePc();
    pc.iceGatheringState = "complete";
    await expect(waitIceGathering(pc as unknown as RTCPeerConnection, 2500)).resolves.toBeUndefined();
  });
});

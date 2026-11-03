// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectTransport, clientTag, netTag, qualityCounters, summarizeInput, summarizeQuality } from "./directTransport";

/** A getStats map the way Chrome shapes it: video inbound-rtp + one nominated pair. */
function statsMap(v: { fps: number; freezes: number; lost: number; received: number; bytes: number; jitter: number; rtt: number }) {
  return new Map<string, Record<string, unknown>>([
    ["IT01V", { type: "inbound-rtp", kind: "video", framesPerSecond: v.fps, freezeCount: v.freezes, packetsLost: v.lost, packetsReceived: v.received, bytesReceived: v.bytes, jitter: v.jitter }],
    ["IT01A", { type: "inbound-rtp", kind: "audio", packetsLost: 999, bytesReceived: 999 }],
    ["CPold", { type: "candidate-pair", nominated: false, state: "failed", currentRoundTripTime: 9 }],
    ["CPok", { type: "candidate-pair", nominated: true, state: "succeeded", currentRoundTripTime: v.rtt }],
  ]);
}

class FakeChannel {
  readyState = "connecting";
  binaryType = "arraybuffer";
  onopen: null | (() => void) = null;
  onmessage: null | ((ev: { data: unknown }) => void) = null;
  sent: string[] = [];
  constructor(readonly label: string) {}
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.readyState = "closed";
  }
}

class FakePc {
  static all: FakePc[] = [];
  static stats = statsMap({ fps: 0, freezes: 0, lost: 0, received: 0, bytes: 0, jitter: 0, rtt: 0 });
  connectionState = "new";
  iceGatheringState = "complete";
  signalingState = "stable";
  localDescription: { type: string; sdp: string } | null = null;
  onconnectionstatechange: null | (() => void) = null;
  channels: FakeChannel[] = [];
  constructor() {
    FakePc.all.push(this);
  }
  createDataChannel(label: string) {
    const ch = new FakeChannel(label);
    this.channels.push(ch);
    return ch;
  }
  addTransceiver() {
    return { setCodecPreferences() {} };
  }
  async createOffer() {
    return { type: "offer", sdp: "v=0" };
  }
  async setLocalDescription(d: { type: string; sdp: string }) {
    this.localDescription = d;
  }
  async setRemoteDescription() {}
  async getStats() {
    return FakePc.stats;
  }
  close() {
    this.signalingState = "closed";
  }
  up() {
    this.connectionState = "connected";
    this.onconnectionstatechange?.();
    for (const ch of this.channels) {
      ch.readyState = "open";
      ch.onopen?.();
    }
  }
  drop() {
    this.connectionState = "failed";
    this.onconnectionstatechange?.();
  }
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

async function liveTransport(reports: Record<string, unknown>[]) {
  const t = new DirectTransport({ bridge: "local", desktopOrigin: "", panelVersion: "1" });
  (t as unknown as { bridge: unknown }).bridge = {
    connect: async () => ({ answer: { type: "answer", sdp: "v=0" }, desktop: { version: "1", protocol: 1 } }),
    report: (p: Record<string, unknown>) => reports.push(p),
  };
  await t.connect();
  await flush();
  FakePc.all[FakePc.all.length - 1].up();
  await flush();
  return t;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakePc.all = [];
  FakePc.stats = statsMap({ fps: 30, freezes: 1, lost: 10, received: 1000, bytes: 0, jitter: 0.004, rtt: 0.04 });
  (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = FakePc;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("qualityCounters", () => {
  it("reads only the video inbound-rtp and the nominated, succeeded pair", () => {
    const q = qualityCounters(statsMap({ fps: 24, freezes: 2, lost: 5, received: 500, bytes: 1234, jitter: 0.012, rtt: 0.042 }));
    expect(q).toEqual({ fps: 24, freezes: 2, lost: 5, received: 500, bytes: 1234, jitterMs: 12, rttMs: 42 });
  });

  it("is all zeros when nothing is flowing yet", () => {
    expect(qualityCounters(new Map())).toEqual({ fps: 0, freezes: 0, lost: 0, received: 0, bytes: 0, jitterMs: 0, rttMs: 0 });
  });
});

describe("summarizeQuality", () => {
  it("averages fps, takes worst jitter/rtt, and deltas the cumulative counters", () => {
    const base = { fps: 0, freezes: 1, lost: 10, received: 1000, bytes: 0, jitterMs: 0, rttMs: 0 };
    const a = { fps: 30, freezes: 2, lost: 15, received: 1500, bytes: 3_750_000, jitterMs: 5, rttMs: 40 };
    const b = { fps: 20, freezes: 4, lost: 20, received: 1990, bytes: 7_500_000, jitterMs: 9, rttMs: 80 };
    expect(summarizeQuality(base, [a, b], 60_000)).toEqual({ fps: 25, freezes: 3, loss_pct: 1, jitter_ms: 9, kbps: 1000, rtt_ms: 80 });
  });
});

describe("clientTag / netTag", () => {
  it("buckets the UA and connection, never passing them through", () => {
    expect(clientTag("Mozilla/5.0 (Linux; Android 14) Chrome/130 UEFNDuckyAndroid/1.2")).toBe("android_app");
    expect(clientTag("Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile")).toBe("phone_web");
    expect(clientTag("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari")).toBe("phone_web");
    expect(clientTag("Mozilla/5.0 (iPad; CPU OS 18_0) Safari")).toBe("tablet_web");
    expect(clientTag("Mozilla/5.0 (Linux; Android 14) Chrome/130")).toBe("tablet_web");
    expect(clientTag("Mozilla/5.0 (Windows NT 10.0) Chrome/130")).toBe("desktop_web");
    expect(netTag({ type: "cellular", effectiveType: "4g" })).toBe("cell");
    expect(netTag({ type: "wifi" })).toBe("wifi");
    expect(netTag({ effectiveType: "3g" })).toBe("unknown");
    expect(netTag({ type: "bluetooth", effectiveType: "weird" })).toBe("unknown");
    expect(netTag(undefined)).toBe("unknown");
  });
});

describe("load and session end", () => {
  const ends = (reports: Record<string, unknown>[]) => reports.filter((r) => "end" in r).map((r) => r.end);

  it("reports first frame once per page load", async () => {
    const reports: Record<string, unknown>[] = [];
    const t = await liveTransport(reports);
    t.reportFirstFrame();
    t.reportFirstFrame();
    const load = reports.filter((r) => "load" in r);
    expect(load).toHaveLength(1);
    expect(load[0]).toMatchObject({ load: { first_frame_ms: expect.any(Number) }, client: "desktop_web" });
  });

  it("sends background when hidden while live, and only one end per session", async () => {
    const reports: Record<string, unknown>[] = [];
    await liveTransport(reports);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    FakePc.all[0].drop();
    expect(ends(reports)).toEqual([{ why: "background" }]);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  });

  it("sends network when a live link fails, again after it was live once more", async () => {
    const reports: Record<string, unknown>[] = [];
    await liveTransport(reports);
    FakePc.all[0].drop();
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    FakePc.all[1].drop(); // a failed redial is the same session
    expect(ends(reports)).toEqual([{ why: "network" }]);
    await vi.advanceTimersByTimeAsync(2_000);
    await flush();
    FakePc.all[2].up();
    FakePc.all[2].drop();
    expect(ends(reports)).toEqual([{ why: "network" }, { why: "network" }]);
  });

  it("passes on the Android app's ud:app-end reason", async () => {
    const reports: Record<string, unknown>[] = [];
    await liveTransport(reports);
    document.dispatchEvent(new CustomEvent("ud:app-end", { detail: { why: "crash" } }));
    expect(ends(reports)).toEqual([{ why: "crash" }]);
  });

  it("reports a crash the app left in localStorage, and clears it", () => {
    const reports: Record<string, unknown>[] = [];
    const orig = DirectTransport.prototype["reportHealth" as keyof DirectTransport] as unknown;
    localStorage.setItem("ud-last-end", "crash");
    (DirectTransport.prototype as unknown as { reportHealth: (p: Record<string, unknown>) => void }).reportHealth = (p) => reports.push(p);
    try {
      new DirectTransport({ bridge: "local", desktopOrigin: "", panelVersion: "1" });
    } finally {
      (DirectTransport.prototype as unknown as { reportHealth: unknown }).reportHealth = orig;
    }
    expect(reports).toEqual([{ end: { why: "crash" } }]);
    expect(localStorage.getItem("ud-last-end")).toBeNull();
  });
});

describe("summarizeInput", () => {
  it("gives median and worst round trip plus the lost count", () => {
    expect(summarizeInput([80, 20, 50], 1)).toEqual({ probes: 4, lost: 1, p50_ms: 50, max_ms: 80 });
    expect(summarizeInput([], 2)).toEqual({ probes: 2, lost: 2, p50_ms: 0, max_ms: 0 });
  });
});

describe("DirectTransport input lag", () => {
  it("tags every 10th input with a seq, times its echo, and counts unanswered probes once a minute", async () => {
    const reports: Record<string, unknown>[] = [];
    const t = await liveTransport(reports);
    const ch = FakePc.all[0].channels.find((c) => c.label === "input")!;
    for (let i = 0; i < 20; i += 1) expect(t.sendInput({ type: "move", x: i })).toBe(true);
    const sent = ch.sent.map((s) => JSON.parse(s) as { seq?: number; x: number });
    expect(sent.filter((m) => m.seq !== undefined).map((m) => [m.x, m.seq])).toEqual([[9, 1], [19, 2]]);
    ch.onmessage?.({ data: JSON.stringify({ echo: 1 }) });
    ch.onmessage?.({ data: JSON.stringify({ echo: 99 }) }); // unknown seq: ignored
    await vi.advanceTimersByTimeAsync(60_000);
    const input = reports.filter((r) => "input" in r).map((r) => r.input as Record<string, number>);
    expect(input).toHaveLength(1);
    expect(input[0]).toMatchObject({ probes: 2, lost: 1 });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(reports.filter((r) => "input" in r)).toHaveLength(1); // a quiet minute sends nothing
  });
});

describe("DirectTransport quality reports", () => {
  it("sends one aggregated quality report per minute while live, none after the link drops", async () => {
    const reports: Record<string, unknown>[] = [];
    await liveTransport(reports);
    const quality = () => reports.filter((r) => "quality" in r).map((r) => r.quality);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(quality()).toHaveLength(0);
    FakePc.stats = statsMap({ fps: 30, freezes: 3, lost: 20, received: 1990, bytes: 7_500_000, jitter: 0.009, rtt: 0.08 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(quality()).toEqual([{ fps: 30, freezes: 2, loss_pct: 1, jitter_ms: 9, kbps: 1000, rtt_ms: 80 }]);
    expect(reports.find((r) => "quality" in r)).toMatchObject({ client: "desktop_web", net: "unknown", viewer_version: "1" });
    FakePc.all[0].drop();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(quality()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(quality()).toHaveLength(1);
  });
});

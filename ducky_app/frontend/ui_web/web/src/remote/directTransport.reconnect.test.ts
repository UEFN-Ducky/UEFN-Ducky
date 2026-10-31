// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectTransport, REMOTE_RESYNC } from "./directTransport";

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
  connectionState = "new";
  iceGatheringState = "complete";
  signalingState = "stable";
  localDescription: { type: string; sdp: string } | null = null;
  onconnectionstatechange: null | (() => void) = null;
  oniceconnectionstatechange: null | (() => void) = null;
  onicegatheringstatechange: null | (() => void) = null;
  onsignalingstatechange: null | (() => void) = null;
  ontrack: null | (() => void) = null;
  ondatachannel: null | (() => void) = null;
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
  addEventListener() {}
  removeEventListener() {}
  async getStats() {
    return new Map();
  }
  close() {
    this.signalingState = "closed";
  }
  /** The link comes up: connected, then every channel opens. */
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

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => (hidden ? "hidden" : "visible") });
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

function transport() {
  const t = new DirectTransport({ bridge: "local", desktopOrigin: "", panelVersion: "1" });
  (t as unknown as { bridge: unknown }).bridge = {
    connect: async () => ({ answer: { type: "answer", sdp: "v=0" }, desktop: { version: "1", protocol: 1 } }),
    report: () => {},
  };
  return t;
}

async function liveTransport() {
  const t = transport();
  await t.connect();
  await flush();
  FakePc.all[FakePc.all.length - 1].up();
  return t;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakePc.all = [];
  (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = FakePc;
  setHidden(false);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("a link that drops after it was live (a phone that slept)", () => {
  it("says reconnecting, not failed, and redials on its own", async () => {
    const t = await liveTransport();
    expect(t.status.state).toBe("live");
    FakePc.all[0].drop();
    expect(t.status.state).toBe("reconnecting");
    expect(FakePc.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakePc.all).toHaveLength(2);
    expect(t.status.state).toBe("reconnecting");
  });

  it("waits while hidden and dials the moment the page is shown again", async () => {
    const t = await liveTransport();
    setHidden(true);
    FakePc.all[0].drop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakePc.all).toHaveLength(1);
    setHidden(false);
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(FakePc.all).toHaveLength(2);
    expect(t.status.state).toBe("reconnecting");
  });

  it("tells the views to reload once it is back, exactly once", async () => {
    const t = await liveTransport();
    const seen: string[] = [];
    t.onEvent((e) => seen.push(String(e.type)));
    FakePc.all[0].drop();
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    FakePc.all[1].up();
    expect(t.status.state).toBe("live");
    expect(seen.filter((s) => s === REMOTE_RESYNC)).toHaveLength(1);
  });

  it("holds a call made while reconnecting and sends it on the new link", async () => {
    const t = await liveTransport();
    FakePc.all[0].drop();
    const call = t.invoke("ping", []);
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    const pc = FakePc.all[1];
    pc.up();
    const rpc = pc.channels.find((c) => c.label === "rpc")!;
    const req = JSON.parse(rpc.sent[0]) as { id: number; method: string };
    expect(req.method).toBe("ping");
    rpc.onmessage?.({ data: JSON.stringify({ id: req.id, ok: true, result: "pong" }) });
    await expect(call).resolves.toBe("pong");
  });

  it("backs off between failed redials, capped at 15 s", async () => {
    const t = await liveTransport();
    FakePc.all[0].drop();
    for (const wait of [1_000, 2_000, 4_000, 8_000, 15_000, 15_000]) {
      const before = FakePc.all.length;
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(FakePc.all).toHaveLength(before);
      await vi.advanceTimersByTimeAsync(1);
      await flush();
      expect(FakePc.all).toHaveLength(before + 1);
      FakePc.all[FakePc.all.length - 1].drop();
    }
    expect(t.status.state).toBe("reconnecting");
  });

  it("asks again for a picture that was mid-download when the link dropped", async () => {
    const t = await liveTransport();
    const pic = t.fetchBlob("/chat-attachments/c1/s1.png");
    const first = FakePc.all[0].channels.find((c) => c.label === "blob")!;
    expect(JSON.parse(first.sent[0])).toMatchObject({ get: "/chat-attachments/c1/s1.png" });
    FakePc.all[0].drop();
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    const pc = FakePc.all[1];
    pc.up();
    const blob = pc.channels.find((c) => c.label === "blob")!;
    const req = JSON.parse(blob.sent[0]) as { id: number; get: string };
    expect(req.get).toBe("/chat-attachments/c1/s1.png");
    blob.onmessage?.({ data: JSON.stringify({ id: req.id, status: 200, type: "image/png" }) });
    const chunk = new Uint8Array([0, 0, 0, 0, 7, 8, 9]);
    new DataView(chunk.buffer).setUint32(0, req.id);
    blob.onmessage?.({ data: chunk.buffer });
    blob.onmessage?.({ data: JSON.stringify({ id: req.id, done: true }) });
    const res = await pic;
    expect(res.status).toBe(200);
    expect(Array.from(new Uint8Array(await res.arrayBuffer()))).toEqual([7, 8, 9]);
  });

  it("keeps a slow download going while bytes arrive; gives up after 60 s of silence", async () => {
    const t = await liveTransport();
    const video = t.fetchBlob("/generated-images/clip.mp4");
    const blob = FakePc.all[0].channels.find((c) => c.label === "blob")!;
    const id = (JSON.parse(blob.sent[0]) as { id: number }).id;
    blob.onmessage?.({ data: JSON.stringify({ id, status: 200, type: "video/mp4" }) });
    for (let i = 0; i < 4; i += 1) {
      await vi.advanceTimersByTimeAsync(50_000); // 200 s in total: past the old flat 60 s limit
      const chunk = new Uint8Array([0, 0, 0, 0, i]);
      new DataView(chunk.buffer).setUint32(0, id);
      blob.onmessage?.({ data: chunk.buffer });
    }
    blob.onmessage?.({ data: JSON.stringify({ id, done: true }) });
    expect(Array.from(new Uint8Array(await (await video).arrayBuffer()))).toEqual([0, 1, 2, 3]);

    const stalled = t.fetchBlob("/generated-images/stuck.mp4");
    const caught = stalled.catch((e: Error) => e.message);
    await vi.advanceTimersByTimeAsync(60_000);
    await expect(caught).resolves.toBe("blob timeout");
  });

  it("a link that never went live still fails (wrong PC, PC offline)", async () => {
    const t = transport();
    await t.connect();
    await flush();
    FakePc.all[0].drop();
    expect(t.status.state).toBe("failed");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakePc.all).toHaveLength(1);
  });
});

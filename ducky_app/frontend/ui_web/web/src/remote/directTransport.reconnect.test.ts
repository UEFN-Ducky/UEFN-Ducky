// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectTransport, HEALTH_OUTBOX_KEY, REMOTE_RESYNC, UD_DIRECT_REPORT_ACK } from "./directTransport";

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
  pcOf(t).up();
  return t;
}

/** This transport's own peer connection (earlier tests' transports still listen on document). */
function pcOf(t: DirectTransport): FakePc {
  return (t as unknown as { pc: FakePc }).pc;
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

  it("resends a call whose answer a drop lost, with the same rid, and it resolves once", async () => {
    const t = await liveTransport();
    const call = t.invoke("send_message", ["c1", "hi"]);
    await flush();
    const first = pcOf(t).channels.find((c) => c.label === "rpc")!;
    const sent = JSON.parse(first.sent[0]) as { id: number; rid: string; method: string };
    expect(sent.method).toBe("send_message");
    expect(sent.rid).toMatch(/^[a-z0-9]+:\d+$/);
    pcOf(t).drop(); // the desktop ran it, but the answer never arrived
    await vi.advanceTimersByTimeAsync(1_000);
    await flush();
    const pc = pcOf(t);
    pc.up();
    await flush();
    const rpc = pc.channels.find((c) => c.label === "rpc")!;
    const again = JSON.parse(rpc.sent[0]) as { id: number; rid: string };
    expect(again.rid).toBe(sent.rid);
    rpc.onmessage?.({ data: JSON.stringify({ id: again.id, ok: true, result: "sent once" }) });
    await expect(call).resolves.toBe("sent once");
  });

  it("1,000 calls across 5 drops: all delivered, each rid run once", async () => {
    const t = await liveTransport();
    const runs = new Map<string, number>();
    const answers = new Map<string, unknown>();
    const results: Array<Promise<unknown>> = [];
    let n = 0;
    // The desktop: runs a rid the first time it sees it, answers a resend from its record.
    const serve = (answerEvery: number) => {
      const rpc = pcOf(t).channels.find((c) => c.label === "rpc")!;
      const reqs = rpc.sent.splice(0).map((s) => JSON.parse(s) as { id: number; rid: string });
      reqs.forEach((req, i) => {
        if (!answers.has(req.rid)) {
          runs.set(req.rid, (runs.get(req.rid) ?? 0) + 1);
          answers.set(req.rid, req.rid);
        }
        if (i % answerEvery === 0) rpc.onmessage?.({ data: JSON.stringify({ id: req.id, ok: true, result: answers.get(req.rid) }) });
      });
    };
    for (let round = 0; round < 6; round += 1) {
      if (round < 5) for (let i = 0; i < 200; i += 1) results.push(t.invoke("ping_effect", [n++]));
      await flush();
      serve(round < 5 ? 2 : 1); // half the answers lost to the drop, the last round answers all
      if (round === 5) break;
      pcOf(t).drop();
      await vi.advanceTimersByTimeAsync(1_000);
      await flush();
      pcOf(t).up();
      await flush();
    }
    const got = await Promise.all(results);
    expect(got).toHaveLength(1000);
    expect(new Set(got).size).toBe(1000);
    expect(runs.size).toBe(1000);
    expect([...runs.values()].every((c) => c === 1)).toBe(true);
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

  it("redials a link that still says connected when the Android app moves network, same session", async () => {
    const sessions: string[] = [];
    const t = transport();
    (t as unknown as { bridge: { connect: unknown } }).bridge.connect = async (session: string) => {
      sessions.push(session);
      return { answer: { type: "answer", sdp: "v=0" }, desktop: { version: "1", protocol: 1 } };
    };
    await t.connect();
    await flush();
    const old = pcOf(t);
    old.up();
    expect(t.status.state).toBe("live");
    expect(old.connectionState).toBe("connected");
    document.dispatchEvent(new CustomEvent("ud:net-change", { detail: { kind: "cell" } }));
    await vi.advanceTimersByTimeAsync(5_000);
    await flush();
    const next = pcOf(t);
    expect(next).not.toBe(old);
    expect(old.signalingState).toBe("closed");
    expect(sessions).toHaveLength(2);
    expect(sessions[1]).toBe(sessions[0]);
    expect(t.session).toBe(sessions[0]);
    next.up();
    expect(t.status.state).toBe("live");
  });

  it("the Android app's resume (ud:app-wake) redials a dropped link at once", async () => {
    const t = await liveTransport();
    setHidden(true);
    const old = pcOf(t);
    old.drop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect((t as unknown as { pc: FakePc | null }).pc).toBeNull();
    setHidden(false);
    document.dispatchEvent(new CustomEvent("ud:app-wake", { detail: { why: "resume" } }));
    await flush();
    expect(pcOf(t)).not.toBe(old);
    expect(t.status.state).toBe("reconnecting");
  });

  it("a live link that is fine ignores ud:app-wake", async () => {
    const t = await liveTransport();
    const pc = pcOf(t);
    document.dispatchEvent(new CustomEvent("ud:app-wake", { detail: { why: "resume" } }));
    await flush();
    expect(pcOf(t)).toBe(pc);
    expect(t.status.state).toBe("live");
  });

  it("keeps health reports until the site acks them and resends the rest on the next start", async () => {
    window.localStorage.removeItem(HEALTH_OUTBOX_KEY);
    const t = transport();
    const sent: Array<Record<string, unknown>> = [];
    (t as unknown as { bridge: { report: unknown } }).bridge.report = (p: Record<string, unknown>) => sent.push(p);
    const health = (t as unknown as { reportHealth: (p: Record<string, unknown>) => void }).reportHealth.bind(t);
    health({ end: { why: "background" } });
    health({ load: { first_frame_ms: 900 } });
    expect(sent).toHaveLength(2);
    const [acked, lost] = sent.map((p) => String(p.rid));
    expect(acked).not.toBe(lost);
    const outbox = () => JSON.parse(window.localStorage.getItem(HEALTH_OUTBOX_KEY) || "[]") as Array<{ rid: string }>;
    expect(outbox().map((r) => r.rid)).toEqual([acked, lost]);
    window.dispatchEvent(new MessageEvent("message", { data: { type: UD_DIRECT_REPORT_ACK, rid: acked } }));
    expect(outbox().map((r) => r.rid)).toEqual([lost]);
    // The app was killed before the site took the second one: the next page start sends it again, once.
    const posted: unknown[] = [];
    const spy = vi.spyOn(window, "postMessage").mockImplementation((m: unknown) => void posted.push(m));
    new DirectTransport({ bridge: "parent", panelVersion: "1" });
    spy.mockRestore();
    expect(posted).toEqual([expect.objectContaining({ type: "ud-direct-report", rid: lost, load: { first_frame_ms: 900 } })]);
    expect(outbox()).toEqual([]);
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

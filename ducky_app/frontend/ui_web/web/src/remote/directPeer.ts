/**
 * Desktop side of Remote View direct mode. Runs in the app's WebView2 page.
 * Answers one full-ICE offer per viewer session (delivered as a `direct_rtc`
 * panel event), then serves the DataChannels:
 *
 *   rpc     → PanelApi calls (same REMOTE_DENY allowlist as /__panel_api)
 *   events  → the agent event bus, pushed as they happen
 *   blob    → assets under the allowed prefixes, streamed in 16 KiB chunks
 *   input   → window_input for the watched window
 *   stream:*→ bridged to the local terminal / LSP WebSockets
 *
 * Video: the viewer's offer carries a recvonly video transceiver; we answer
 * sendonly and attach the cropped screen track via replaceTrack when the
 * viewer asks to watch a window. No renegotiation ever.
 */
import { getApi } from "../hooks/usePanelApi";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent } from "../types/panel";
import {
  BLOB_LOW_WATER_BYTES,
  CHANNEL_BLOB,
  CHANNEL_EVENTS,
  CHANNEL_INPUT,
  CHANNEL_RPC,
  DEFAULT_ICE,
  MAX_CHUNK_BYTES,
  PROTOCOL_VERSION,
  PartAssembler,
  STREAM_PREFIX,
  blobPathAllowed,
  sdpFingerprint,
  splitText,
  type BlobRequest,
  type RpcRequest,
  type StreamOpen,
} from "./protocol";
import { acquireScreenTrack, croppedTrack, onScreenEnded, preferVideoCodecs, releaseScreenTrack, tuneSender } from "./remoteCapture";

const ICE_GATHER_MS = 2500;
const LOCAL_WS = /^ws:\/\/(127\.0\.0\.1|localhost):\d+(\/.*)?$/;

type Session = {
  id: string;
  pc: RTCPeerConnection;
  videoSender: RTCRtpSender | null;
  watch: { hwnd: string; abort: AbortController } | null;
  watchJob: Promise<unknown>;
  unsubEvents: (() => void) | null;
  eventSeq: number;
  streams: Map<string, WebSocket>;
  parts: PartAssembler;
  denied: Set<string>;
};

const sessions = new Map<string, Session>();
let installed = false;
let deniedPromise: Promise<Set<string>> | null = null;

function deniedMethods(): Promise<Set<string>> {
  if (deniedPromise) return deniedPromise;
  const api = getApi();
  deniedPromise = Promise.resolve(api?.remote_deny_methods?.() ?? [])
    .then((rows) => new Set((rows as string[]).map(String)))
    .catch(() => new Set<string>());
  return deniedPromise;
}

export function installDirectPeer(): () => void {
  if (installed) return () => {};
  installed = true;
  installAgentEventBus();
  const unsub = subscribeAgentEvents((event) => {
    if (event.type === "direct_rtc") void handleOffer(event);
  });
  const unsubEnded = onScreenEnded(() => {
    for (const s of sessions.values()) stopWatch(s);
  });
  return () => {
    installed = false;
    unsub();
    unsubEnded();
    for (const id of [...sessions.keys()]) closeSession(id);
  };
}

async function handleOffer(event: AgentEvent) {
  const session = String((event as unknown as { session?: string }).session || "");
  const offer = (event as unknown as { offer?: { type: string; sdp: string } }).offer;
  const ice = ((event as unknown as { ice?: RTCIceServer[] }).ice || []) as RTCIceServer[];
  const api = getApi();
  const answerApi = api?.direct_rtc_answer;
  if (!session || !offer?.sdp || !answerApi) return;
  // The event bus replays its backlog on page load; a stale offer's viewer is
  // long gone and answering it would just spin up a dead peer connection.
  const ts = Number((event as unknown as { ts?: number }).ts || 0);
  if (ts && Date.now() / 1000 - ts > 30) return;
  closeSession(session);
  const pc = new RTCPeerConnection({
    iceServers: ice.length ? ice : DEFAULT_ICE,
    bundlePolicy: "max-bundle",
    rtcpMuxPolicy: "require",
  });
  const s: Session = {
    id: session,
    pc,
    videoSender: null,
    watch: null,
    watchJob: Promise.resolve(),
    unsubEvents: null,
    eventSeq: 0,
    streams: new Map(),
    parts: new PartAssembler(),
    denied: await deniedMethods(),
  };
  sessions.set(session, s);
  pc.ondatachannel = (ev) => wireChannel(s, ev.channel);
  pc.onconnectionstatechange = () => {
    console.debug("[remote-direct] connection", pc.connectionState);
    if (pc.connectionState === "failed") console.error("[remote-direct] peer connection failed", session);
    if (pc.connectionState === "failed" || pc.connectionState === "closed") closeSession(session);
  };
  pc.oniceconnectionstatechange = () => console.debug("[remote-direct] ice", pc.iceConnectionState);
  try {
    await pc.setRemoteDescription({ type: "offer", sdp: offer.sdp });
    const video = pc.getTransceivers().find((t) => t.receiver.track?.kind === "video");
    if (video) {
      video.direction = "sendonly";
      s.videoSender = video.sender;
    }
    preferVideoCodecs(pc);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    await waitIceGathering(pc, ICE_GATHER_MS);
    const local = pc.localDescription;
    if (!local) throw new Error("no local description");
    // ponytail: plain {type, sdp} — pywebview's serializer copies the native
    // toJSON off RTCSessionDescription and JSON.stringify then throws.
    await answerApi(session, { type: "answer", sdp: local.sdp }, sdpFingerprint(local.sdp));
    if (s.videoSender) void tuneSender(s.videoSender);
  } catch (err) {
    console.error("[remote-direct] answer failed", err);
    void answerApi(session, null, "", String((err as Error)?.message || err));
    closeSession(session);
  }
}

function closeSession(id: string) {
  const s = sessions.get(id);
  if (!s) return;
  sessions.delete(id);
  stopWatch(s);
  s.unsubEvents?.();
  for (const ws of s.streams.values()) {
    try {
      ws.close();
    } catch {
      /* ignore */
    }
  }
  try {
    s.pc.close();
  } catch {
    /* ignore */
  }
}

function wireChannel(s: Session, ch: RTCDataChannel) {
  ch.binaryType = "arraybuffer";
  switch (ch.label) {
    case CHANNEL_RPC:
      ch.onmessage = (ev) => typeof ev.data === "string" && void onRpc(s, ch, ev.data);
      break;
    case CHANNEL_EVENTS:
      s.unsubEvents?.();
      s.unsubEvents = subscribeAgentEvents((event) => {
        if (event.type === "direct_rtc" || ch.readyState !== "open") return;
        s.eventSeq += 1;
        const text = JSON.stringify({ seq: s.eventSeq, event });
        const pieces = splitText(text);
        if (pieces.length === 1) ch.send(text);
        else pieces.forEach((data, part) => ch.send(JSON.stringify({ seq: s.eventSeq, part, of: pieces.length, data })));
      });
      break;
    case CHANNEL_BLOB:
      ch.onmessage = (ev) => typeof ev.data === "string" && void onBlob(s, ch, ev.data);
      break;
    case CHANNEL_INPUT:
      ch.onmessage = (ev) => {
        if (typeof ev.data !== "string" || !s.watch) return;
        try {
          void getApi()?.window_input?.(s.watch.hwnd, JSON.parse(ev.data) as Record<string, unknown>);
        } catch {
          /* ignore */
        }
      };
      break;
    default:
      break;
  }
}

async function onRpc(s: Session, ch: RTCDataChannel, raw: string) {
  let req: RpcRequest;
  try {
    const parsed = JSON.parse(raw) as RpcRequest & { part?: number; of?: number; data?: string };
    if (typeof parsed.part === "number" && typeof parsed.of === "number") {
      const full = s.parts.push(parsed.id, parsed.part, parsed.of, parsed.data || "");
      if (full === null) return;
      req = JSON.parse(full) as RpcRequest;
    } else req = parsed;
  } catch {
    return;
  }
  const reply = (payload: Record<string, unknown>) => {
    if (ch.readyState !== "open") return;
    const text = JSON.stringify(payload);
    const pieces = splitText(text);
    if (pieces.length === 1) ch.send(text);
    else pieces.forEach((data, part) => ch.send(JSON.stringify({ id: req.id, ok: true, part, of: pieces.length, data })));
  };
  const method = String(req.method || "");
  const args = Array.isArray(req.args) ? req.args : [];
  if (method === "__stream_open") {
    reply({ id: req.id, ...openStream(s, args[0] as StreamOpen & { id: string }) });
    return;
  }
  if (method === "watch_window") {
    const hwnd = String((args[0] as { hwnd?: string })?.hwnd || "");
    const job = s.watchJob.catch(() => {}).then(() => startWatch(s, hwnd));
    s.watchJob = job;
    reply({ id: req.id, ...(await job) });
    return;
  }
  if (method === "__ping") {
    reply({ id: req.id, ok: true, result: { protocol: PROTOCOL_VERSION, ts: Date.now() } });
    return;
  }
  const api = getApi() as unknown as Record<string, (...a: unknown[]) => unknown> | null;
  if (!api || !method || method.startsWith("_") || s.denied.has(method)) {
    reply({ id: req.id, ok: false, error: "method not allowed" });
    return;
  }
  if (typeof api[method] !== "function") {
    // Same wording the HTTP path uses for an unknown method so callers that
    // already tolerate it keep tolerating it; the detail helps the e2e driver.
    reply({ id: req.id, ok: false, error: `method not allowed (no such method: ${method})` });
    return;
  }
  try {
    const result = await api[method](...args);
    reply({ id: req.id, ok: true, result: result === undefined ? null : result });
  } catch (err) {
    reply({ id: req.id, ok: false, error: String((err as Error)?.message || err) });
  }
}

async function startWatch(s: Session, hwnd: string): Promise<{ ok: boolean; error?: string; result?: unknown }> {
  stopWatch(s);
  if (!hwnd) return { ok: true, result: { watching: "" } };
  if (!s.videoSender) return { ok: false, error: "no video transceiver in offer" };
  const abort = new AbortController();
  let held = false;
  try {
    const screen = await acquireScreenTrack();
    held = true;
    const track = await croppedTrack(screen, hwnd, abort.signal);
    abort.signal.addEventListener("abort", () => {
      try {
        track.stop();
      } catch {
        /* ignore */
      }
      if (held) {
        held = false;
        releaseScreenTrack();
      }
    });
    await s.videoSender.replaceTrack(track);
    s.watch = { hwnd, abort };
    void tuneSender(s.videoSender);
    void getApi()?.window_input?.(hwnd, { type: "focus" });
    return { ok: true, result: { watching: hwnd } };
  } catch (err) {
    abort.abort();
    if (held) {
      held = false;
      releaseScreenTrack();
    }
    return { ok: false, error: `capture failed: ${String((err as Error)?.message || err)}` };
  }
}

function stopWatch(s: Session) {
  const w = s.watch;
  if (!w) return;
  s.watch = null;
  w.abort.abort();
  if (s.videoSender) void s.videoSender.replaceTrack(null).catch(() => {});
}

async function onBlob(_s: Session, ch: RTCDataChannel, raw: string) {
  let req: BlobRequest;
  try {
    req = JSON.parse(raw) as BlobRequest;
  } catch {
    return;
  }
  const send = (obj: Record<string, unknown>) => ch.readyState === "open" && ch.send(JSON.stringify(obj));
  const path = String(req.get || "");
  if (!blobPathAllowed(path)) {
    send({ id: req.id, status: 403 });
    return;
  }
  try {
    const headers: Record<string, string> = {};
    if (req.etag) headers["If-None-Match"] = req.etag;
    const res = await fetch(path, { headers, cache: "no-store" });
    if (res.status === 304) {
      send({ id: req.id, status: 304, etag: req.etag });
      return;
    }
    if (!res.ok) {
      send({ id: req.id, status: res.status });
      return;
    }
    const buf = new Uint8Array(await res.arrayBuffer());
    send({ id: req.id, status: 200, type: res.headers.get("Content-Type") || "", etag: res.headers.get("ETag") || "", size: buf.byteLength });
    ch.bufferedAmountLowThreshold = BLOB_LOW_WATER_BYTES;
    const payload = MAX_CHUNK_BYTES - 4;
    for (let off = 0; off < buf.byteLength; off += payload) {
      if (ch.readyState !== "open") return;
      if (ch.bufferedAmount > BLOB_LOW_WATER_BYTES) {
        await new Promise<void>((resolve) => {
          const onLow = () => {
            ch.removeEventListener("bufferedamountlow", onLow);
            resolve();
          };
          ch.addEventListener("bufferedamountlow", onLow);
        });
      }
      const slice = buf.subarray(off, Math.min(buf.byteLength, off + payload));
      const frame = new Uint8Array(4 + slice.byteLength);
      new DataView(frame.buffer).setUint32(0, req.id);
      frame.set(slice, 4);
      ch.send(frame);
    }
    send({ id: req.id, done: true });
  } catch (err) {
    send({ id: req.id, status: 502, error: String((err as Error)?.message || err) });
  }
}

function openStream(s: Session, open: StreamOpen & { id?: string }): { ok: boolean; error?: string; result?: unknown } {
  const id = String(open?.id || "");
  const url = String(open?.url || "");
  if (!id.startsWith(STREAM_PREFIX) || !LOCAL_WS.test(url)) return { ok: false, error: "stream not allowed" };
  const ch = s.pc.createDataChannel(id, { ordered: true });
  ch.binaryType = "arraybuffer";
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  s.streams.set(id, ws);
  const pendingToWs: Array<string | ArrayBuffer> = [];
  ws.onopen = () => {
    for (const m of pendingToWs.splice(0)) ws.send(m);
  };
  ws.onmessage = (ev) => {
    if (ch.readyState !== "open") return;
    if (typeof ev.data === "string") {
      for (const piece of splitText(ev.data)) ch.send(piece);
    } else ch.send(ev.data as ArrayBuffer);
  };
  ws.onclose = () => {
    s.streams.delete(id);
    try {
      ch.close();
    } catch {
      /* ignore */
    }
  };
  ws.onerror = () => ws.close();
  ch.onmessage = (ev) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(ev.data as string | ArrayBuffer);
    else if (ws.readyState === WebSocket.CONNECTING) pendingToWs.push(ev.data as string | ArrayBuffer);
  };
  ch.onclose = () => {
    s.streams.delete(id);
    try {
      ws.close();
    } catch {
      /* ignore */
    }
  };
  return { ok: true, result: { id } };
}

function waitIceGathering(pc: RTCPeerConnection, maxMs: number): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => {
      if (pc.iceGatheringState === "complete") done();
    };
    pc.addEventListener("icegatheringstatechange", check);
    window.setTimeout(done, maxMs);
  });
}

export function directPeerSessionCount(): number {
  return sessions.size;
}

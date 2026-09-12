import { useEffect } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent, WindowBox } from "../types/panel";
import { rankVideoCodec } from "./remoteWindowMath";

export { rankVideoCodec } from "./remoteWindowMath";

/**
 * Desktop side of Remote View. Runs inside the app's WebView2 (Chromium):
 * one getDisplayMedia screen track for the whole app lifetime, one
 * RTCPeerConnection per remote viewer session, crop to the watched window
 * done in a Worker so the panel UI thread never stalls the frame pump.
 *
 * ponytail: signaling for a session is serialized and the open is memoised —
 * an offer and its trickle candidates land in the same event batch and used
 * to spawn N capture requests + N peer connections (N "sharing your screen"
 * bars, candidates applied to the wrong pc, ICE failing on real NATs).
 */

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun.cloudflare.com:3478" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
  bundlePolicy: "max-bundle",
  rtcpMuxPolicy: "require",
};

const MAX_BITRATE = 20_000_000;
const MAX_FPS = 60;
const BOX_POLL_MS = 250;
// Drop the screen capture (and its "sharing your screen" bar) once nobody
// has watched for this long; a viewer retry inside the window reuses it.
const IDLE_RELEASE_MS = 5000;

type Session = {
  pc: RTCPeerConnection;
  hwnd: string;
  abort: AbortController;
  pendingIce: RTCIceCandidateInit[];
  haveRemote: boolean;
  /** Local candidates gathered before the answer was signaled. */
  outgoingIce: RTCIceCandidateInit[];
  answerSent: boolean;
  queue: Promise<void>;
};

type CropCtx = {
  onmessage: ((ev: { data: CropMessage }) => void) | null;
};
type CropMessage =
  | { type: "box"; box: WindowBox | null }
  | { type: "start"; readable: ReadableStream<VideoFrame>; writable: WritableStream<VideoFrame> };

let screenPromise: Promise<MediaStreamTrack> | null = null;
let idleTimer = 0;
const sessions = new Map<string, Session>();
const opening = new Map<string, Promise<Session | null>>();

function releaseScreenWhenIdle() {
  window.clearTimeout(idleTimer);
  idleTimer = window.setTimeout(() => {
    if (sessions.size || opening.size || !screenPromise) return;
    const p = screenPromise;
    screenPromise = null;
    void p.then((track) => track.stop()).catch(() => {});
  }, IDLE_RELEASE_MS);
}

function screenTrack(): Promise<MediaStreamTrack> {
  if (screenPromise) {
    return screenPromise.then((track) => {
      if (track.readyState === "live") return track;
      screenPromise = null;
      return screenTrack();
    });
  }
  const opts = {
    video: {
      frameRate: { ideal: MAX_FPS, max: MAX_FPS },
      displaySurface: "monitor",
    },
    audio: false,
    selfBrowserSurface: "exclude",
    surfaceSwitching: "exclude",
    systemAudio: "exclude",
    monitorTypeSurfaces: "include",
    preferCurrentTab: false,
  } as unknown as DisplayMediaStreamOptions;
  screenPromise = navigator.mediaDevices.getDisplayMedia(opts).then((stream) => {
    const track = stream.getVideoTracks()[0];
    if (!track) throw new Error("no screen track");
    try {
      track.contentHint = "motion";
    } catch {
      /* ignore */
    }
    track.addEventListener("ended", () => {
      screenPromise = null;
      for (const id of [...sessions.keys()]) teardown(id);
    });
    return track;
  });
  screenPromise.catch(() => {
    screenPromise = null;
  });
  return screenPromise;
}

/**
 * Crop pump. Self-contained on purpose: it is stringified into a Worker
 * (no closure access) and also runs on the main thread as a fallback.
 * Frames are re-wrapped with a visibleRect (metadata only, no copy) and
 * dropped instead of queued when the encoder is behind — latency over
 * completeness.
 */
function cropPump(ctx: CropCtx) {
  let box: WindowBox | null = null;
  const rectFor = (frame: VideoFrame): DOMRectInit => {
    const fw = frame.displayWidth;
    const fh = frame.displayHeight;
    const b = box;
    if (!b || !(b.right && b.bottom)) return { x: 0, y: 0, width: fw, height: fh };
    const virtual = Math.abs(fw - (b.screen_w || 0)) <= 4;
    const ox = virtual ? b.screen_left || 0 : 0;
    const oy = virtual ? b.screen_top || 0 : 0;
    let x = Math.max(0, Math.min(fw - 2, (b.left || 0) - ox));
    let y = Math.max(0, Math.min(fh - 2, (b.top || 0) - oy));
    let w = Math.max(2, Math.min(fw - x, (b.right || 0) - (b.left || 0)));
    let h = Math.max(2, Math.min(fh - y, (b.bottom || 0) - (b.top || 0)));
    x -= x % 2;
    y -= y % 2;
    w -= w % 2;
    h -= h % 2;
    return { x, y, width: Math.max(2, w), height: Math.max(2, h) };
  };
  const pump = async (readable: ReadableStream<VideoFrame>, writable: WritableStream<VideoFrame>) => {
    const reader = readable.getReader();
    const writer = writable.getWriter();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done || !value) break;
        try {
          if (writer.desiredSize !== null && writer.desiredSize < 0) continue;
          const r = rectFor(value);
          const full =
            r.x === 0 && r.y === 0 && r.width === value.displayWidth && r.height === value.displayHeight;
          if (full) {
            await writer.write(value);
            continue;
          }
          const next = new VideoFrame(value, { visibleRect: r });
          try {
            await writer.write(next);
          } finally {
            next.close();
          }
        } finally {
          value.close();
        }
      }
    } catch {
      /* closed */
    } finally {
      try {
        writer.releaseLock();
      } catch {
        /* ignore */
      }
      try {
        reader.releaseLock();
      } catch {
        /* ignore */
      }
    }
  };
  ctx.onmessage = (ev) => {
    const m = ev.data;
    if (m.type === "box") box = m.box;
    else if (m.type === "start") void pump(m.readable, m.writable);
  };
}

function startCropWorker(
  readable: ReadableStream<VideoFrame>,
  writable: WritableStream<VideoFrame>,
): { post: (m: CropMessage) => void; stop: () => void } {
  try {
    const src = `(${cropPump.toString()})(self);`;
    const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    const worker = new Worker(url);
    URL.revokeObjectURL(url);
    worker.postMessage({ type: "start", readable, writable }, [readable, writable] as unknown as Transferable[]);
    return {
      post: (m) => worker.postMessage(m),
      stop: () => worker.terminate(),
    };
  } catch {
    const ctx: CropCtx = { onmessage: null };
    cropPump(ctx);
    ctx.onmessage?.({ data: { type: "start", readable, writable } });
    return {
      post: (m) => ctx.onmessage?.({ data: m }),
      stop: () => {},
    };
  }
}

async function croppedTrack(
  screen: MediaStreamTrack,
  hwnd: string,
  abort: AbortSignal,
): Promise<MediaStreamTrack> {
  const Processor = (
    window as unknown as {
      MediaStreamTrackProcessor?: new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<VideoFrame> };
    }
  ).MediaStreamTrackProcessor;
  const Generator = (
    window as unknown as {
      MediaStreamTrackGenerator?: new (init: { kind: "video" }) => MediaStreamTrack & {
        writable: WritableStream<VideoFrame>;
      };
    }
  ).MediaStreamTrackGenerator;
  if (!Processor || !Generator) throw new Error("no crop");
  const clone = screen.clone();
  const processor = new Processor({ track: clone });
  const generator = new Generator({ kind: "video" });
  const worker = startCropWorker(processor.readable, generator.writable);
  const api = getApi();
  const pollBox = () => {
    if (!api?.window_box) return;
    void api.window_box(hwnd).then(
      (next) => {
        if (next?.right) worker.post({ type: "box", box: next });
      },
      () => {},
    );
  };
  pollBox();
  const poll = window.setInterval(pollBox, BOX_POLL_MS);
  abort.addEventListener("abort", () => {
    window.clearInterval(poll);
    worker.stop();
    try {
      clone.stop();
    } catch {
      /* ignore */
    }
    try {
      generator.stop();
    } catch {
      /* ignore */
    }
  });
  return generator;
}

function preferVideoCodecs(pc: RTCPeerConnection) {
  const caps = RTCRtpSender.getCapabilities?.("video");
  if (!caps) return;
  const ordered = [...caps.codecs].sort((a, b) => rankVideoCodec(a) - rankVideoCodec(b));
  for (const t of pc.getTransceivers()) {
    if (t.sender.track?.kind !== "video") continue;
    try {
      t.setCodecPreferences(ordered);
    } catch {
      /* ignore */
    }
  }
}

async function tuneSender(pc: RTCPeerConnection) {
  const sender = pc.getSenders().find((s) => s.track?.kind === "video");
  if (!sender) return;
  try {
    const params = sender.getParameters();
    if (!params.encodings?.length) params.encodings = [{}];
    for (const enc of params.encodings) {
      enc.maxBitrate = MAX_BITRATE;
      enc.maxFramerate = MAX_FPS;
      enc.scaleResolutionDownBy = 1;
      (enc as { priority?: string }).priority = "high";
      (enc as { networkPriority?: string }).networkPriority = "high";
    }
    (params as { degradationPreference?: string }).degradationPreference = "maintain-framerate";
    await sender.setParameters(params);
  } catch {
    /* ignore */
  }
}

function errorText(err: unknown): string {
  if (err instanceof Error) return `${err.name}: ${err.message}`;
  return String(err);
}

function teardown(sessionId: string) {
  const inflight = opening.get(sessionId);
  if (inflight) {
    void inflight.then((s) => {
      if (s && sessions.get(sessionId) === s) teardown(sessionId);
    });
  }
  const session = sessions.get(sessionId);
  if (!session) return;
  sessions.delete(sessionId);
  session.abort.abort();
  try {
    session.pc.close();
  } catch {
    /* ignore */
  }
  if (!sessions.size) releaseScreenWhenIdle();
}

async function openSession(sessionId: string, hwnd: string): Promise<Session | null> {
  const api = getApi();
  const signal = api?.rtc_signal;
  const inject = api?.window_input;
  if (!signal) return null;
  const abort = new AbortController();
  window.clearTimeout(idleTimer);
  let track: MediaStreamTrack;
  try {
    const screen = await screenTrack();
    track = await croppedTrack(screen, hwnd, abort.signal);
  } catch (err) {
    abort.abort();
    console.error("[remote-view] capture failed", err);
    void signal(sessionId, { type: "rtc", kind: "fail", stage: "capture", error: errorText(err) });
    return null;
  }
  const pc = new RTCPeerConnection(STUN);
  pc.addTrack(track);
  pc.onicecandidate = (ev) => {
    if (!ev.candidate) return;
    const c = ev.candidate.toJSON();
    const s = sessions.get(sessionId);
    if (s && !s.answerSent) {
      s.outgoingIce.push(c);
      return;
    }
    void signal(sessionId, { type: "rtc", candidate: c });
  };
  pc.oniceconnectionstatechange = () => console.debug("[remote-view] ice", pc.iceConnectionState);
  pc.onconnectionstatechange = () => {
    console.debug("[remote-view] connection", pc.connectionState);
    if (pc.connectionState === "failed" || pc.connectionState === "closed") teardown(sessionId);
  };
  pc.ondatachannel = (ev) => {
    ev.channel.onmessage = (msg) => {
      if (typeof msg.data !== "string") return;
      try {
        const event = JSON.parse(msg.data) as Record<string, unknown>;
        void inject?.(hwnd, event);
      } catch {
        /* ignore */
      }
    };
  };
  const session: Session = {
    pc,
    hwnd,
    abort,
    pendingIce: [],
    haveRemote: false,
    outgoingIce: [],
    answerSent: false,
    queue: Promise.resolve(),
  };
  sessions.set(sessionId, session);
  return session;
}

async function applySignal(sessionId: string, session: Session, payload: Record<string, unknown>) {
  const { pc } = session;
  const sdp = payload.sdp as RTCSessionDescriptionInit | undefined;
  const candidate = payload.candidate as RTCIceCandidateInit | undefined;
  if (sdp?.type === "offer") {
    await pc.setRemoteDescription(sdp);
    preferVideoCodecs(pc);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    session.haveRemote = true;
    const queued = session.pendingIce.splice(0);
    for (const c of queued) {
      await pc.addIceCandidate(c).catch(() => {});
    }
    void tuneSender(pc);
    // ponytail: plain {type, sdp} — pywebview's serializer copies the native
    // toJSON off RTCSessionDescription and JSON.stringify then throws
    // "Illegal invocation"; the answer never left the desktop.
    const local = pc.localDescription;
    const signal = getApi()?.rtc_signal;
    await signal?.(sessionId, {
      type: "rtc",
      sdp: local ? { type: local.type, sdp: local.sdp } : null,
    });
    session.answerSent = true;
    for (const c of session.outgoingIce.splice(0)) {
      await signal?.(sessionId, { type: "rtc", candidate: c });
    }
    return;
  }
  if (candidate) {
    if (!session.haveRemote) session.pendingIce.push(candidate);
    else await pc.addIceCandidate(candidate).catch(() => {});
  }
}

async function handleRtcEvent(event: AgentEvent) {
  const sessionId = String(event.session_id || "");
  const hwnd = String(event.hwnd || "");
  const payload = event.payload || {};
  if (!sessionId) return;
  if (payload.kind === "close" || payload.kind === "fail") {
    teardown(sessionId);
    return;
  }
  if (!hwnd) return;
  let session = sessions.get(sessionId);
  if (!session) {
    let inflight = opening.get(sessionId);
    if (!inflight) {
      inflight = openSession(sessionId, hwnd).finally(() => opening.delete(sessionId));
      opening.set(sessionId, inflight);
    }
    session = (await inflight) ?? undefined;
    if (!session || sessions.get(sessionId) !== session) return;
  }
  const s = session;
  s.queue = s.queue
    .then(() => applySignal(sessionId, s, payload))
    .catch((err: unknown) => {
      console.error("[remote-view] negotiation failed", err);
      teardown(sessionId);
      void getApi()?.rtc_signal?.(sessionId, {
        type: "rtc",
        kind: "fail",
        stage: "negotiate",
        error: errorText(err),
      });
    });
}

export function RemoteWindowSender() {
  useEffect(() => {
    if (isRemote()) return;
    installAgentEventBus();
    return subscribeAgentEvents((event) => {
      if (event.type !== "window_rtc") return;
      void handleRtcEvent(event);
    });
  }, []);
  return null;
}

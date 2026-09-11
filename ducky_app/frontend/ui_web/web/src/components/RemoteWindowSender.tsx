import { useEffect } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent, WindowBox } from "../types/panel";

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun.cloudflare.com:3478" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
};

type Session = {
  pc: RTCPeerConnection;
  hwnd: string;
  abort: AbortController;
};

let cachedScreen: MediaStreamTrack | null = null;
const sessions = new Map<string, Session>();

async function screenTrack(): Promise<MediaStreamTrack> {
  if (cachedScreen && cachedScreen.readyState === "live") return cachedScreen;
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: { frameRate: 60 },
    audio: false,
  });
  const track = stream.getVideoTracks()[0];
  if (!track) throw new Error("no screen track");
  try {
    track.contentHint = "motion";
  } catch {
    /* ignore */
  }
  cachedScreen = track;
  return track;
}

function cropRect(frame: VideoFrame, box: WindowBox | null): DOMRectInit {
  const fw = frame.displayWidth;
  const fh = frame.displayHeight;
  if (!box || !(box.right && box.bottom)) {
    return { x: 0, y: 0, width: fw, height: fh };
  }
  const virtual = Math.abs(fw - (box.screen_w || 0)) <= 4;
  const ox = virtual ? box.screen_left || 0 : 0;
  const oy = virtual ? box.screen_top || 0 : 0;
  let x = Math.max(0, Math.min(fw - 2, (box.left || 0) - ox));
  let y = Math.max(0, Math.min(fh - 2, (box.top || 0) - oy));
  let w = Math.max(2, Math.min(fw - x, (box.right || 0) - (box.left || 0)));
  let h = Math.max(2, Math.min(fh - y, (box.bottom || 0) - (box.top || 0)));
  x -= x % 2;
  y -= y % 2;
  w -= w % 2;
  h -= h % 2;
  return { x, y, width: Math.max(2, w), height: Math.max(2, h) };
}

async function croppedTrack(
  screen: MediaStreamTrack,
  hwnd: string,
  abort: AbortSignal,
): Promise<MediaStreamTrack> {
  const Processor = (
    window as unknown as { MediaStreamTrackProcessor?: new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<VideoFrame> } }
  ).MediaStreamTrackProcessor;
  const Generator = (
    window as unknown as { MediaStreamTrackGenerator?: new (init: { kind: "video" }) => MediaStreamTrack & { writable: WritableStream<VideoFrame> } }
  ).MediaStreamTrackGenerator;
  if (!Processor || !Generator) throw new Error("no crop");
  const boxRef: { current: WindowBox | null } = { current: null };
  const poll = window.setInterval(() => {
    const api = getApi();
    if (!api?.window_box) return;
    void api.window_box(hwnd).then((next) => {
      if (next?.right) boxRef.current = next;
    });
  }, 250);
  abort.addEventListener("abort", () => window.clearInterval(poll));
  const clone = screen.clone();
  const processor = new Processor({ track: clone });
  const generator = new Generator({ kind: "video" });
  const writer = generator.writable.getWriter();
  const reader = processor.readable.getReader();
  const pump = async () => {
    try {
      while (!abort.aborted) {
        const { value, done } = await reader.read();
        if (done || !value) break;
        try {
          const next = new VideoFrame(value, { visibleRect: cropRect(value, boxRef.current) });
          await writer.write(next);
          next.close();
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
        clone.stop();
      } catch {
        /* ignore */
      }
    }
  };
  void pump();
  return generator;
}

function preferVideoCodecs(pc: RTCPeerConnection) {
  const caps = RTCRtpSender.getCapabilities?.("video");
  if (!caps) return;
  const rank = (mime: string) => {
    const x = mime.toLowerCase();
    if (x.includes("h264")) return 0;
    if (x.includes("av1")) return 1;
    if (x.includes("vp9")) return 2;
    return 9;
  };
  const ordered = [...caps.codecs].sort((a, b) => rank(a.mimeType) - rank(b.mimeType));
  for (const t of pc.getTransceivers()) {
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
      enc.maxBitrate = 12_000_000;
      enc.maxFramerate = 60;
    }
    await sender.setParameters(params);
  } catch {
    /* ignore */
  }
}

function teardown(sessionId: string) {
  const session = sessions.get(sessionId);
  if (!session) return;
  sessions.delete(sessionId);
  session.abort.abort();
  try {
    session.pc.close();
  } catch {
    /* ignore */
  }
}

async function ensureSession(sessionId: string, hwnd: string): Promise<Session | null> {
  const existing = sessions.get(sessionId);
  if (existing) return existing;
  const api = getApi();
  const signal = api?.rtc_signal;
  const inject = api?.window_input;
  if (!signal) return null;
  const abort = new AbortController();
  let track: MediaStreamTrack;
  try {
    const screen = await screenTrack();
    track = await croppedTrack(screen, hwnd, abort.signal);
  } catch {
    void signal(sessionId, { type: "rtc", kind: "fail" });
    return null;
  }
  const pc = new RTCPeerConnection(STUN);
  preferVideoCodecs(pc);
  pc.addTrack(track);
  void tuneSender(pc);
  pc.onicecandidate = (ev) => {
    if (!ev.candidate) return;
    void signal(sessionId, { type: "rtc", candidate: ev.candidate.toJSON() });
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
  abort.signal.addEventListener("abort", () => {
    try {
      track.stop();
    } catch {
      /* ignore */
    }
  });
  const session: Session = { pc, hwnd, abort };
  sessions.set(sessionId, session);
  return session;
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
  const session = await ensureSession(sessionId, hwnd);
  if (!session) return;
  const { pc } = session;
  const sdp = payload.sdp as RTCSessionDescriptionInit | undefined;
  const candidate = payload.candidate as RTCIceCandidateInit | undefined;
  try {
    if (sdp?.type === "offer") {
      await pc.setRemoteDescription(sdp);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      const api = getApi();
      await api?.rtc_signal?.(sessionId, { type: "rtc", sdp: pc.localDescription });
      return;
    }
    if (candidate) {
      try {
        await pc.addIceCandidate(candidate);
      } catch {
        /* trickle before setRemoteDescription */
      }
    }
  } catch {
    teardown(sessionId);
    const api = getApi();
    void api?.rtc_signal?.(sessionId, { type: "rtc", kind: "fail" });
  }
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

import { useEffect } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent } from "../types/panel";
import { acquireScreenTrack, croppedTrack, onScreenEnded, preferVideoCodecs, releaseScreenTrack, tuneSender } from "../remote/remoteCapture";

export { rankVideoCodec } from "./remoteWindowMath";

/**
 * Desktop side of the tunnel Remote View path. Runs inside the app's WebView2
 * (Chromium): one shared screen capture (see remote/remoteCapture.ts), one
 * RTCPeerConnection per remote viewer session signaled over the tunnel
 * WebSocket. The direct (tunnel-free) path lives in remote/directPeer.ts and
 * shares the capture module.
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

const sessions = new Map<string, Session>();
const opening = new Map<string, Promise<Session | null>>();

onScreenEnded(() => {
  for (const id of [...sessions.keys()]) teardown(id);
});

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
}

async function openSession(sessionId: string, hwnd: string): Promise<Session | null> {
  const api = getApi();
  const signal = api?.rtc_signal;
  const inject = api?.window_input;
  if (!signal) return null;
  const abort = new AbortController();
  let track: MediaStreamTrack;
  try {
    const screen = await acquireScreenTrack();
    abort.signal.addEventListener("abort", () => releaseScreenTrack());
    track = await croppedTrack(screen, hwnd, abort.signal);
  } catch (err) {
    abort.abort();
    console.error("[remote-view] capture failed", err);
    void signal(sessionId, { type: "rtc", kind: "fail", stage: "capture", error: errorText(err) });
    return null;
  }
  const pc = new RTCPeerConnection(STUN);
  const sender = pc.addTrack(track);
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
  abort.signal.addEventListener("abort", () => {
    try {
      track.stop();
    } catch {
      /* ignore */
    }
  });
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
  void sender;
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
    const videoSender = pc.getSenders().find((s) => s.track?.kind === "video");
    if (videoSender) void tuneSender(videoSender);
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

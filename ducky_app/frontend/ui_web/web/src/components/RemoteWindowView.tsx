import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceDropdown } from "./ChoiceDropdown";
import { contentRect, rankVideoCodec } from "./remoteWindowMath";

export { contentRect } from "./remoteWindowMath";

export type WindowViewRow = { id: string; title: string; kind?: string };

/**
 * Browser side of Remote View: WebRTC only. The tunnel WebSocket carries
 * SDP/ICE and the window-fit size; video and input are peer-to-peer.
 * No JPEG fallback — a failed peer connection shows why and retries.
 */

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun.cloudflare.com:3478" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
  bundlePolicy: "max-bundle",
  rtcpMuxPolicy: "require",
};

const CONNECT_MS = 12_000;
const RETRY_MS = [1500, 3000, 5000, 8000];
const SIZE_DEBOUNCE_MS = 400;

export function RemoteWindowSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const [rows, setRows] = useState<WindowViewRow[]>([]);

  const load = useCallback(async () => {
    if (!isRemote()) return;
    const api = getApi();
    if (!api?.list_window_views) return;
    try {
      const next = await api.list_window_views();
      if (Array.isArray(next)) setRows(next);
    } catch {
      setRows([]);
    }
  }, []);

  useEffect(() => {
    if (!isRemote()) return;
    void load();
    const id = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(id);
  }, [load]);

  if (!isRemote()) return null;

  return (
    <div className="remote-window-select no-drag" onPointerDown={() => void load()}>
      <ChoiceDropdown
        size="compact"
        aria-label="View"
        value={value}
        minWidth={240}
        placeholder="View"
        fixedLabel="View"
        onChange={onChange}
        options={[
          { value: "", label: "Ducky", group: "This app" },
          ...rows.map((row) => ({
            value: row.id,
            label: row.title,
            group:
              row.kind === "uefn" ? "UEFN" : row.kind === "blender" ? "Blender" : "Windows",
          })),
        ]}
      />
    </div>
  );
}

function wsUrl(hwnd: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/__window_stream?id=${encodeURIComponent(hwnd)}`;
}

type Rect = { left: number; top: number; width: number; height: number };

function videoRect(video: HTMLVideoElement): Rect {
  const r = video.getBoundingClientRect();
  return contentRect(
    { left: r.left, top: r.top, width: r.width, height: r.height },
    video.videoWidth,
    video.videoHeight,
  );
}

function norm(rect: Rect, clientX: number, clientY: number) {
  const w = rect.width || 1;
  const h = rect.height || 1;
  return {
    x: Math.min(1, Math.max(0, (clientX - rect.left) / w)),
    y: Math.min(1, Math.max(0, (clientY - rect.top) / h)),
  };
}

function overlaySize(el: HTMLElement | null): { w: number; h: number } | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 80 || r.height < 80) return null;
  const dpr = window.devicePixelRatio || 1;
  return { w: Math.round(r.width * dpr), h: Math.round(r.height * dpr) };
}

/**
 * Pointer/key capture on the video. Moves are coalesced to one per animation
 * frame (a phone fires 120 Hz pointermove; SendInput on the desktop does not
 * need more than the encoder's frame rate). Down/up flush the pending move
 * first so ordering holds.
 */
function attachInput(video: HTMLVideoElement, send: (payload: Record<string, unknown>) => void) {
  let pendingMove: { x: number; y: number; button: number } | null = null;
  let raf = 0;
  const flush = () => {
    raf = 0;
    if (!pendingMove) return;
    send({ type: "move", ...pendingMove });
    pendingMove = null;
  };
  const point = (ev: { clientX: number; clientY: number }) => norm(videoRect(video), ev.clientX, ev.clientY);
  const onWheel = (ev: WheelEvent) => {
    ev.preventDefault();
    flush();
    send({ type: "wheel", ...point(ev), delta: ev.deltaY > 0 ? 1 : -1 });
  };
  const onDown = (ev: PointerEvent) => {
    ev.preventDefault();
    video.focus({ preventScroll: true });
    try {
      video.setPointerCapture(ev.pointerId);
    } catch {
      /* ignore */
    }
    flush();
    send({ type: "down", ...point(ev), button: ev.button });
  };
  const onUp = (ev: PointerEvent) => {
    flush();
    send({ type: "up", ...point(ev), button: ev.button });
  };
  const onMove = (ev: PointerEvent) => {
    pendingMove = { ...point(ev), button: ev.button };
    if (!raf) raf = requestAnimationFrame(flush);
  };
  const onKeyDown = (ev: KeyboardEvent) => {
    ev.preventDefault();
    if (ev.repeat) return;
    send({ type: "keydown", key: ev.key });
  };
  const onKeyUp = (ev: KeyboardEvent) => {
    ev.preventDefault();
    send({ type: "keyup", key: ev.key });
  };
  const onContext = (ev: Event) => ev.preventDefault();
  video.addEventListener("wheel", onWheel, { passive: false });
  video.addEventListener("pointerdown", onDown);
  video.addEventListener("pointerup", onUp);
  video.addEventListener("pointermove", onMove);
  video.addEventListener("keydown", onKeyDown);
  video.addEventListener("keyup", onKeyUp);
  video.addEventListener("contextmenu", onContext);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    video.removeEventListener("wheel", onWheel);
    video.removeEventListener("pointerdown", onDown);
    video.removeEventListener("pointerup", onUp);
    video.removeEventListener("pointermove", onMove);
    video.removeEventListener("keydown", onKeyDown);
    video.removeEventListener("keyup", onKeyUp);
    video.removeEventListener("contextmenu", onContext);
  };
}

function preferCodecs(transceiver: RTCRtpTransceiver) {
  const caps = RTCRtpReceiver.getCapabilities?.("video");
  if (!caps) return;
  try {
    transceiver.setCodecPreferences(
      [...caps.codecs].sort((a, b) => rankVideoCodec(a) - rankVideoCodec(b)),
    );
  } catch {
    /* Safari < 15.4 */
  }
}

function zeroPlayoutDelay(receiver: RTCRtpReceiver) {
  const r = receiver as RTCRtpReceiver & { jitterBufferTarget?: number; playoutDelayHint?: number };
  try {
    r.jitterBufferTarget = 0;
  } catch {
    /* ignore */
  }
  try {
    r.playoutDelayHint = 0;
  } catch {
    /* ignore */
  }
}

type Phase = "connecting" | "live" | "failed" | "kicked";
type Stats = { codec: string; w: number; h: number; fps: number; kbps: number; rtt: number };

type StatsCursor = { bytes: number; at: number };

async function sampleStats(pc: RTCPeerConnection, cursor: StatsCursor): Promise<Stats | null> {
  const report = await pc.getStats();
  let inbound: Record<string, unknown> | null = null;
  const codecs = new Map<string, string>();
  let rtt = 0;
  report.forEach((row) => {
    const r = row as Record<string, unknown>;
    if (r.type === "inbound-rtp" && r.kind === "video") inbound = r;
    else if (r.type === "codec") codecs.set(String(r.id), String(r.mimeType || ""));
    else if (r.type === "candidate-pair" && (r.nominated || r.state === "succeeded")) {
      const v = Number(r.currentRoundTripTime || 0);
      if (v > 0) rtt = v;
    }
  });
  if (!inbound) return null;
  const row = inbound as Record<string, unknown>;
  const bytes = Number(row.bytesReceived || 0);
  const now = performance.now();
  const dt = Math.max(1, now - cursor.at) / 1000;
  const kbps = cursor.at ? ((bytes - cursor.bytes) * 8) / dt / 1000 : 0;
  cursor.bytes = bytes;
  cursor.at = now;
  const mime = codecs.get(String(row.codecId || "")) || "";
  return {
    codec: mime.split("/")[1] || "",
    w: Number(row.frameWidth || 0),
    h: Number(row.frameHeight || 0),
    fps: Math.round(Number(row.framesPerSecond || 0)),
    kbps: Math.round(kbps),
    rtt: Math.round(rtt * 1000),
  };
}

function statsLabel(s: Stats): string {
  const parts: string[] = [];
  if (s.w && s.h) parts.push(`${s.w}×${s.h}`);
  if (s.codec) parts.push(s.codec);
  parts.push(`${s.fps} fps`);
  parts.push(s.kbps >= 1000 ? `${(s.kbps / 1000).toFixed(1)} Mb/s` : `${s.kbps} kb/s`);
  if (s.rtt) parts.push(`${s.rtt} ms`);
  return parts.join(" · ");
}

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [reason, setReason] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [attempt, setAttempt] = useState(0);
  const attemptRef = useRef(0);

  const retry = useCallback(() => {
    attemptRef.current += 1;
    setAttempt(attemptRef.current);
  }, []);

  useEffect(() => {
    attemptRef.current = 0;
    setAttempt(0);
  }, [hwnd]);

  useEffect(() => {
    if (!hwnd) return;
    let closed = false;
    let live = false;
    let retryTimer = 0;
    setPhase("connecting");
    setReason("");
    setStats(null);

    const fail = (why: string, kind: Phase = "failed") => {
      if (closed) return;
      closed = true;
      setPhase(kind);
      setReason(why);
      window.clearTimeout(connectTimer);
      if (kind === "failed") {
        const delay = RETRY_MS[Math.min(attemptRef.current, RETRY_MS.length - 1)];
        retryTimer = window.setTimeout(retry, delay);
      }
    };

    const ws = new WebSocket(wsUrl(hwnd));
    wsRef.current = ws;
    const pc = new RTCPeerConnection(STUN);
    const transceiver = pc.addTransceiver("video", { direction: "recvonly" });
    preferCodecs(transceiver);
    const dc = pc.createDataChannel("input", { ordered: true });
    dcRef.current = dc;
    const connectTimer = window.setTimeout(
      () => fail("No peer-to-peer path in time (network may block UDP)."),
      CONNECT_MS,
    );
    const sendWs = (payload: Record<string, unknown>) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(payload));
    };
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) return;
      sendWs({ type: "rtc", candidate: ev.candidate.toJSON() });
    };
    pc.ontrack = (ev) => {
      const video = videoRef.current;
      if (!video) return;
      zeroPlayoutDelay(ev.receiver);
      video.srcObject = ev.streams[0] ?? new MediaStream(ev.track ? [ev.track] : []);
      void video.play().catch(() => {});
    };
    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      if (st === "connected") {
        live = true;
        window.clearTimeout(connectTimer);
        setPhase("live");
        setReason("");
        return;
      }
      if (st === "failed") fail(live ? "Peer connection dropped." : "Peer connection failed (network may block UDP).");
    };
    ws.onopen = () => {
      const size = overlaySize(overlayRef.current);
      if (size) sendWs({ type: "size", ...size });
      void (async () => {
        try {
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          sendWs({ type: "rtc", sdp: pc.localDescription });
        } catch (err) {
          fail(`Could not create offer: ${String((err as Error)?.message || err)}`);
        }
      })();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      try {
        const msg = JSON.parse(ev.data) as {
          type?: string;
          kind?: string;
          sdp?: RTCSessionDescriptionInit;
          candidate?: RTCIceCandidateInit;
        };
        if (msg.kind === "kicked") {
          fail("Another browser took over this view.", "kicked");
          return;
        }
        if (msg.kind === "fail") {
          fail("Desktop could not start screen capture.");
          return;
        }
        if (msg.kind === "close") {
          fail("Desktop closed the stream.");
          return;
        }
        if (msg.sdp) void pc.setRemoteDescription(msg.sdp).catch(() => fail("Bad answer from desktop."));
        else if (msg.candidate) void pc.addIceCandidate(msg.candidate).catch(() => {});
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      if (!live) fail("Signaling channel closed before the stream started.");
    };
    ws.onerror = () => {
      if (!live) fail("Signaling channel error.");
    };

    const cursor: StatsCursor = { bytes: 0, at: 0 };
    const statsTimer = window.setInterval(() => {
      if (pc.connectionState !== "connected") return;
      void sampleStats(pc, cursor).then((s) => {
        if (s && !closed) setStats(s);
      });
    }, 1000);

    return () => {
      closed = true;
      window.clearTimeout(connectTimer);
      window.clearTimeout(retryTimer);
      window.clearInterval(statsTimer);
      wsRef.current = null;
      dcRef.current = null;
      for (const close of [() => dc.close(), () => pc.close(), () => ws.close()]) {
        try {
          close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [hwnd, attempt, retry]);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el || !hwnd) return;
    let timer = 0;
    const ro = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const size = overlaySize(el);
        if (!size) return;
        const payload = JSON.stringify({ type: "size", ...size });
        const dc = dcRef.current;
        if (dc && dc.readyState === "open") {
          dc.send(payload);
          return;
        }
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(payload);
      }, SIZE_DEBOUNCE_MS);
    });
    ro.observe(el);
    return () => {
      window.clearTimeout(timer);
      ro.disconnect();
    };
  }, [hwnd, attempt]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !hwnd || phase !== "live") return;
    return attachInput(el, (payload) => {
      const text = JSON.stringify(payload);
      const dc = dcRef.current;
      if (dc && dc.readyState === "open") {
        dc.send(text);
        return;
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(text);
    });
  }, [hwnd, phase, attempt]);

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      <video
        ref={videoRef}
        className={phase === "live" ? "remote-window-canvas" : "remote-window-canvas is-offscreen"}
        autoPlay
        muted
        playsInline
        disablePictureInPicture
        tabIndex={0}
      />
      {phase !== "live" ? (
        <div className="remote-window-status">
          <p className="remote-window-overlay-msg">
            {phase === "connecting" ? "Connecting to desktop…" : reason}
          </p>
          {phase === "kicked" ? (
            <button type="button" className="remote-window-retry" onClick={retry}>
              Take over
            </button>
          ) : phase === "failed" ? (
            <button type="button" className="remote-window-retry" onClick={retry}>
              Retry now
            </button>
          ) : null}
        </div>
      ) : null}
      {phase === "live" && stats ? (
        <span className="remote-window-stats">{statsLabel(stats)}</span>
      ) : null}
    </div>
  );
}

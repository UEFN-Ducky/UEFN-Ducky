import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceDropdown } from "./ChoiceDropdown";

export type WindowViewRow = { id: string; title: string; kind?: string };

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun.cloudflare.com:3478" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
};

const ICE_MS = 8000;

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

function wsUrl(hwnd: string, mode: "rtc" | "jpeg"): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const extra = mode === "jpeg" ? "&mode=jpeg" : "";
  return `${proto}//${location.host}/__window_stream?id=${encodeURIComponent(hwnd)}${extra}`;
}

function normPoint(ev: { currentTarget: HTMLElement; clientX: number; clientY: number }) {
  const r = ev.currentTarget.getBoundingClientRect();
  const w = r.width || 1;
  const h = r.height || 1;
  return {
    x: Math.min(1, Math.max(0, (ev.clientX - r.left) / w)),
    y: Math.min(1, Math.max(0, (ev.clientY - r.top) / h)),
  };
}

function sendOverlaySize(send: (payload: Record<string, unknown>) => void, el: HTMLElement | null) {
  if (!el) return;
  const r = el.getBoundingClientRect();
  if (r.width < 80 || r.height < 80) return;
  const dpr = window.devicePixelRatio || 1;
  send({
    type: "size",
    w: Math.round(r.width * dpr),
    h: Math.round(r.height * dpr),
  });
}

function attachInput(
  el: HTMLElement,
  send: (payload: Record<string, unknown>) => void,
  lastMove: { current: number },
) {
  const onWheel = (ev: WheelEvent) => {
    ev.preventDefault();
    const r = el.getBoundingClientRect();
    const w = r.width || 1;
    const h = r.height || 1;
    send({
      type: "wheel",
      x: Math.min(1, Math.max(0, (ev.clientX - r.left) / w)),
      y: Math.min(1, Math.max(0, (ev.clientY - r.top) / h)),
      delta: ev.deltaY > 0 ? 1 : -1,
    });
  };
  const onDown = (ev: PointerEvent) => {
    ev.preventDefault();
    el.focus();
    el.setPointerCapture(ev.pointerId);
    const { x, y } = normPoint({ currentTarget: el, clientX: ev.clientX, clientY: ev.clientY });
    send({ type: "down", x, y, button: ev.button });
  };
  const onUp = (ev: PointerEvent) => {
    const { x, y } = normPoint({ currentTarget: el, clientX: ev.clientX, clientY: ev.clientY });
    send({ type: "up", x, y, button: ev.button });
  };
  const onMove = (ev: PointerEvent) => {
    const now = performance.now();
    if (ev.buttons === 0 && now - lastMove.current < 25) return;
    lastMove.current = now;
    const { x, y } = normPoint({ currentTarget: el, clientX: ev.clientX, clientY: ev.clientY });
    send({ type: "move", x, y, button: ev.button });
  };
  const onKeyDown = (ev: KeyboardEvent) => {
    ev.preventDefault();
    send({ type: "keydown", key: ev.key });
  };
  const onKeyUp = (ev: KeyboardEvent) => {
    ev.preventDefault();
    send({ type: "keyup", key: ev.key });
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("keydown", onKeyDown);
  el.addEventListener("keyup", onKeyUp);
  return () => {
    el.removeEventListener("wheel", onWheel);
    el.removeEventListener("pointerdown", onDown);
    el.removeEventListener("pointerup", onUp);
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("keydown", onKeyDown);
    el.removeEventListener("keyup", onKeyUp);
  };
}

function JpegOverlay({ hwnd }: { hwnd: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const lastMove = useRef(0);
  const lastSize = useRef(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hwnd) return;
    setFailed(false);
    const ws = new WebSocket(wsUrl(hwnd, "jpeg"));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    let gotFrame = false;
    let frameGen = 0;
    const paint = (src: CanvasImageSource, w: number, h: number, gen: number) => {
      if (gen !== frameGen) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      canvas.getContext("2d")?.drawImage(src, 0, 0);
      gotFrame = true;
      setFailed(false);
    };
    const send = (payload: Record<string, unknown>) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(payload));
    };
    ws.onopen = () => sendOverlaySize(send, overlayRef.current);
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return;
      const gen = ++frameGen;
      const blob = new Blob([ev.data], { type: "image/jpeg" });
      if (typeof createImageBitmap === "function") {
        void createImageBitmap(blob).then(
          (bmp) => {
            try {
              paint(bmp, bmp.width, bmp.height, gen);
            } finally {
              bmp.close();
            }
          },
          () => {},
        );
        return;
      }
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        paint(img, img.width, img.height, gen);
        URL.revokeObjectURL(url);
      };
      img.onerror = () => URL.revokeObjectURL(url);
      img.src = url;
    };
    ws.onclose = () => {
      if (wsRef.current === ws && !gotFrame) setFailed(true);
    };
    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, [hwnd]);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el || !hwnd || failed) return;
    const ro = new ResizeObserver(() => {
      const now = performance.now();
      if (now - lastSize.current < 200) return;
      lastSize.current = now;
      const sock = wsRef.current;
      if (!sock || sock.readyState !== WebSocket.OPEN) return;
      sendOverlaySize((payload) => sock.send(JSON.stringify(payload)), el);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [hwnd, failed]);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el || !hwnd || failed) return;
    return attachInput(el, (payload) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(payload));
    }, lastMove);
  }, [hwnd, failed]);

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      {failed ? (
        <p className="remote-window-overlay-msg">Window unavailable (minimized or closed).</p>
      ) : (
        <canvas ref={canvasRef} tabIndex={0} className="remote-window-canvas" />
      )}
    </div>
  );
}

function RtcOverlay({ hwnd, onFallback }: { hwnd: string; onFallback: () => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const lastMove = useRef(0);
  const lastSize = useRef(0);

  useEffect(() => {
    if (!hwnd) return;
    let settled = false;
    const fallback = () => {
      if (settled) return;
      settled = true;
      onFallback();
    };
    const ws = new WebSocket(wsUrl(hwnd, "rtc"));
    wsRef.current = ws;
    const pc = new RTCPeerConnection(STUN);
    pc.addTransceiver("video", { direction: "recvonly" });
    const dc = pc.createDataChannel("input");
    dcRef.current = dc;
    const timer = window.setTimeout(fallback, ICE_MS);
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
      video.srcObject = ev.streams[0] ?? new MediaStream(ev.track ? [ev.track] : []);
      settled = true;
      window.clearTimeout(timer);
    };
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "closed") fallback();
    };
    ws.onopen = () => {
      sendOverlaySize(sendWs, overlayRef.current);
      void (async () => {
        try {
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          sendWs({ type: "rtc", sdp: pc.localDescription });
        } catch {
          fallback();
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
        if (msg.kind === "fail" || msg.kind === "close") {
          fallback();
          return;
        }
        if (msg.sdp) void pc.setRemoteDescription(msg.sdp);
        else if (msg.candidate) void pc.addIceCandidate(msg.candidate);
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      if (!settled) fallback();
    };
    return () => {
      window.clearTimeout(timer);
      wsRef.current = null;
      dcRef.current = null;
      try {
        dc.close();
      } catch {
        /* ignore */
      }
      try {
        pc.close();
      } catch {
        /* ignore */
      }
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }, [hwnd, onFallback]);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el || !hwnd) return;
    const ro = new ResizeObserver(() => {
      const now = performance.now();
      if (now - lastSize.current < 200) return;
      lastSize.current = now;
      sendOverlaySize((payload) => {
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
      }, el);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [hwnd]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !hwnd) return;
    return attachInput(el, (payload) => {
      const dc = dcRef.current;
      if (dc && dc.readyState === "open") {
        dc.send(JSON.stringify(payload));
        return;
      }
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(payload));
    }, lastMove);
  }, [hwnd]);

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      <video
        ref={videoRef}
        className="remote-window-canvas"
        autoPlay
        muted
        playsInline
        tabIndex={0}
      />
    </div>
  );
}

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const [mode, setMode] = useState<"rtc" | "jpeg">("rtc");
  const onFallback = useCallback(() => setMode("jpeg"), []);

  useEffect(() => {
    setMode("rtc");
  }, [hwnd]);

  if (!hwnd) return null;
  if (mode === "jpeg") return <JpegOverlay hwnd={hwnd} />;
  return <RtcOverlay hwnd={hwnd} onFallback={onFallback} />;
}

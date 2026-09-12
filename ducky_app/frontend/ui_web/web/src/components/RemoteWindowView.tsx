import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceDropdown } from "./ChoiceDropdown";
import { DropdownPanel } from "./DropdownPanel";
import { Icons } from "../icons/Icons";
import { contentRect, keyDiff, rankVideoCodec, stickLookDelta, stickMoveKeys } from "./remoteWindowMath";
import { getDirectTransport } from "../remote/directTransport";

export { contentRect } from "./remoteWindowMath";

export type WindowViewRow = { id: string; title: string; kind?: string };

type SendFn = (payload: Record<string, unknown>) => void;
type ControlMode = "editor" | "play";
type ControlState = { mode: ControlMode; overlay: boolean; look: number; deadzone: number };

let boundSend: SendFn | null = null;
let controlState: ControlState = { mode: "editor", overlay: true, look: 1, deadzone: 0.28 };
const controlSubs = new Set<() => void>();

function bindRemoteSend(fn: SendFn | null) {
  boundSend = fn;
}

export function remoteViewSend(payload: Record<string, unknown>) {
  boundSend?.(payload);
}

function getControlState(): ControlState {
  return controlState;
}

export function setRemoteViewControls(patch: Partial<ControlState>) {
  controlState = { ...controlState, ...patch };
  controlSubs.forEach((fn) => fn());
}

export function useRemoteViewControls(): ControlState {
  return useSyncExternalStore(
    (onChange) => {
      controlSubs.add(onChange);
      return () => controlSubs.delete(onChange);
    },
    getControlState,
    getControlState,
  );
}

const COMMANDS: { key: string; label: string }[] = [
  { key: " ", label: "Space" },
  { key: "f", label: "F" },
  { key: "g", label: "G" },
  { key: "Escape", label: "Esc" },
];

function useWindowViews(enabled: boolean): WindowViewRow[] {
  const [rows, setRows] = useState<WindowViewRow[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const listViews = getApi()?.list_window_views;
    if (!listViews) return;
    let live = true;
    const load = async () => {
      try {
        const next = await listViews();
        if (live && Array.isArray(next)) setRows(next);
      } catch {
        if (live) setRows([]);
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 4000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [enabled]);
  return rows;
}

export function RemoteViewControls({ hwnd }: { hwnd: string }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const controls = useRemoteViewControls();
  const rows = useWindowViews(isRemote() && !!hwnd);
  const row = rows.find((r) => r.id === hwnd);
  if (!isRemote() || !hwnd || row?.kind !== "uefn") return null;

  const tap = (key: string) => {
    remoteViewSend({ type: "keydown", key });
    window.setTimeout(() => remoteViewSend({ type: "keyup", key }), 80);
  };

  return (
    <div className="remote-view-controls no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger choice-dropdown--compact${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Controls"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="choice-dropdown-trigger-copy">
          <span className="choice-dropdown-trigger-label">Controls</span>
        </span>
        <span className={`choice-dropdown-chevron${open ? " is-open" : ""}`} aria-hidden>
          <Icons.ChevronDown />
        </span>
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} minWidth={220}>
        <div className="plugin-header-menu-list" role="menu">
          <button
            type="button"
            className={`plugin-header-menu-item${controls.mode === "editor" ? " is-active" : ""}`}
            onClick={() => setRemoteViewControls({ mode: "editor" })}
          >
            Editor fly
          </button>
          <button
            type="button"
            className={`plugin-header-menu-item${controls.mode === "play" ? " is-active" : ""}`}
            onClick={() => setRemoteViewControls({ mode: "play" })}
          >
            Play
          </button>
          <button
            type="button"
            className="plugin-header-menu-item"
            onClick={() => setRemoteViewControls({ overlay: !controls.overlay })}
          >
            {controls.overlay ? "Hide sticks" : "Show sticks"}
          </button>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Look {controls.look.toFixed(2)}</span>
            <input
              type="range"
              min={0.25}
              max={3}
              step={0.05}
              value={controls.look}
              onChange={(ev) => setRemoteViewControls({ look: Number(ev.target.value) })}
            />
          </label>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Deadzone {controls.deadzone.toFixed(2)}</span>
            <input
              type="range"
              min={0.1}
              max={0.45}
              step={0.01}
              value={controls.deadzone}
              onChange={(ev) => setRemoteViewControls({ deadzone: Number(ev.target.value) })}
            />
          </label>
          {COMMANDS.map((cmd) => (
            <button
              key={cmd.key}
              type="button"
              className="plugin-header-menu-item"
              onClick={() => tap(cmd.key)}
            >
              {cmd.label}
            </button>
          ))}
        </div>
      </DropdownPanel>
    </div>
  );
}

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
    const listViews = getApi()?.list_window_views;
    if (!listViews) return;
    try {
      const next = await listViews();
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

function UefnStickPad({
  className,
  onChange,
}: {
  className: string;
  onChange: (nx: number, ny: number, active: boolean) => void;
}) {
  const padRef = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState({ x: 0, y: 0 });

  const update = (clientX: number, clientY: number, active: boolean) => {
    const el = padRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const max = Math.max(1, r.width / 2);
    let nx = (clientX - (r.left + r.width / 2)) / max;
    let ny = (clientY - (r.top + r.height / 2)) / max;
    const mag = Math.hypot(nx, ny);
    if (mag > 1) {
      nx /= mag;
      ny /= mag;
    }
    setKnob({ x: nx, y: ny });
    onChange(nx, ny, active);
  };

  const end = (ev: ReactPointerEvent<HTMLDivElement>) => {
    ev.preventDefault();
    ev.stopPropagation();
    setKnob({ x: 0, y: 0 });
    onChange(0, 0, false);
  };

  return (
    <div
      ref={padRef}
      className={className}
      onPointerDown={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        ev.currentTarget.setPointerCapture(ev.pointerId);
        update(ev.clientX, ev.clientY, true);
      }}
      onPointerMove={(ev) => {
        if (!ev.currentTarget.hasPointerCapture(ev.pointerId)) return;
        ev.preventDefault();
        ev.stopPropagation();
        update(ev.clientX, ev.clientY, true);
      }}
      onPointerUp={end}
      onPointerCancel={end}
    >
      <span className="remote-stick-knob" style={{ transform: `translate(${knob.x * 28}px, ${knob.y * 28}px)` }} />
    </div>
  );
}

function holdSend(send: SendFn, key: string, down: boolean) {
  send({ type: down ? "keydown" : "keyup", key });
}

function UefnStickOverlay({
  send,
  mode,
  look,
  deadzone,
}: {
  send: SendFn;
  mode: ControlMode;
  look: number;
  deadzone: number;
}) {
  const moveHeld = useRef<string[]>([]);
  const looking = useRef(false);
  const lookVec = useRef({ nx: 0, ny: 0 });
  const lookRaf = useRef(0);
  const lookTune = useRef({ look, deadzone });
  lookTune.current = { look, deadzone };

  const cancelLook = useCallback(() => {
    if (lookRaf.current) cancelAnimationFrame(lookRaf.current);
    lookRaf.current = 0;
    if (!looking.current) return;
    looking.current = false;
    send({ type: "up", x: 0.5, y: 0.5, button: 2 });
  }, [send]);

  const releaseAll = useCallback(() => {
    for (const key of moveHeld.current) send({ type: "keyup", key });
    moveHeld.current = [];
    cancelLook();
  }, [send, cancelLook]);

  useEffect(() => () => releaseAll(), [releaseAll, mode]);

  const onMove = (nx: number, ny: number, active: boolean) => {
    const next = active ? stickMoveKeys(nx, ny, lookTune.current.deadzone) : [];
    const diff = keyDiff(moveHeld.current, next);
    moveHeld.current = next;
    for (const key of diff.down) send({ type: "keydown", key });
    for (const key of diff.up) send({ type: "keyup", key });
  };

  const onLook = (nx: number, ny: number, active: boolean) => {
    lookVec.current = { nx, ny };
    if (!active) {
      cancelLook();
      return;
    }
    if (!looking.current) {
      looking.current = true;
      send({ type: "move", x: 0.5, y: 0.5 });
      send({ type: "down", x: 0.5, y: 0.5, button: 2 });
    }
    if (lookRaf.current) return;
    const tick = () => {
      if (!looking.current) {
        lookRaf.current = 0;
        return;
      }
      const { dx, dy } = stickLookDelta(
        lookVec.current.nx,
        lookVec.current.ny,
        lookTune.current.look,
        lookTune.current.deadzone,
      );
      if (dx || dy) send({ type: "move", dx, dy });
      lookRaf.current = requestAnimationFrame(tick);
    };
    lookRaf.current = requestAnimationFrame(tick);
  };

  return (
    <div className="remote-sticks">
      <UefnStickPad className="remote-stick remote-stick--left" onChange={onMove} />
      <UefnStickPad className="remote-stick remote-stick--right" onChange={onLook} />
      {mode === "editor" ? (
        <div className="remote-stick-btns remote-stick-btns--left">
          <button
            type="button"
            className="remote-stick-btn"
            onPointerDown={(ev) => {
              ev.preventDefault();
              holdSend(send, "q", true);
            }}
            onPointerUp={() => holdSend(send, "q", false)}
            onPointerCancel={() => holdSend(send, "q", false)}
          >
            Q
          </button>
          <button
            type="button"
            className="remote-stick-btn"
            onPointerDown={(ev) => {
              ev.preventDefault();
              holdSend(send, "e", true);
            }}
            onPointerUp={() => holdSend(send, "e", false)}
            onPointerCancel={() => holdSend(send, "e", false)}
          >
            E
          </button>
        </div>
      ) : (
        <div className="remote-stick-btns remote-stick-btns--right">
          <button
            type="button"
            className="remote-stick-btn"
            onPointerDown={(ev) => {
              ev.preventDefault();
              holdSend(send, " ", true);
            }}
            onPointerUp={() => holdSend(send, " ", false)}
            onPointerCancel={() => holdSend(send, " ", false)}
          >
            Jump
          </button>
        </div>
      )}
    </div>
  );
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
  const controls = useRemoteViewControls();
  const rows = useWindowViews(!!hwnd);
  const kind = rows.find((r) => r.id === hwnd)?.kind;
  const kindRef = useRef(kind);
  if (kind) kindRef.current = kind;
  const watchingUefn = (kind || kindRef.current) === "uefn";

  const send = useCallback((payload: Record<string, unknown>) => {
    const direct = getDirectTransport();
    if (direct && direct.sendInput(payload)) return;
    const text = JSON.stringify(payload);
    const dc = dcRef.current;
    if (dc && dc.readyState === "open") {
      dc.send(text);
      return;
    }
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(text);
  }, []);

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

    // Direct (tunnel-free) mode: the transport already owns the peer
    // connection; ask the desktop to attach the cropped window track.
    const direct = getDirectTransport();
    if (direct) {
      const connectTimer = 0;
      void connectTimer;
      const video = videoRef.current;
      const unsubVideo = direct.onVideo((stream) => {
        if (!video || closed) return;
        video.srcObject = stream;
        if (stream) void video.play().catch(() => {});
      });
      const unsubStatus = direct.onStatus((st) => {
        if (closed) return;
        if (st.state === "failed") fail(st.reason || "Desktop connection lost.");
      });
      void direct.invoke("watch_window", [{ hwnd }]).then(
        () => {
          if (closed) return;
          live = true;
          setPhase("live");
          setReason("");
          for (const r of direct.peer?.getReceivers() ?? []) zeroPlayoutDelay(r);
          const size = overlaySize(overlayRef.current);
          if (size) direct.sendInput({ type: "size", ...size });
        },
        (err: unknown) => fail(`Desktop could not start screen capture: ${String((err as Error)?.message || err)}`),
      );
      const cursor: StatsCursor = { bytes: 0, at: 0 };
      const statsTimer = window.setInterval(() => {
        const pc = direct.peer;
        if (!pc || pc.connectionState !== "connected") return;
        void sampleStats(pc, cursor).then((st) => {
          if (st && !closed) setStats(st);
        });
      }, 1000);
      return () => {
        closed = true;
        window.clearTimeout(retryTimer);
        window.clearInterval(statsTimer);
        unsubVideo();
        unsubStatus();
        if (video) video.srcObject = null;
        void direct.invoke("watch_window", [{ hwnd: "" }]).catch(() => {});
      };
    }

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
    // Trickle candidates can overtake the answer through the tunnel; hold them
    // until setRemoteDescription resolves or addIceCandidate rejects them.
    let haveAnswer = false;
    const pendingIce: RTCIceCandidateInit[] = [];
    const addIce = (c: RTCIceCandidateInit) => {
      if (!haveAnswer) {
        pendingIce.push(c);
        return;
      }
      void pc.addIceCandidate(c).catch((err: unknown) => console.debug("[remote-view] addIceCandidate", err));
    };
    pc.oniceconnectionstatechange = () => console.debug("[remote-view] ice", pc.iceConnectionState);
    pc.onicegatheringstatechange = () => console.debug("[remote-view] gathering", pc.iceGatheringState);
    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      console.debug("[remote-view] connection", st);
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
          stage?: string;
          error?: string;
          sdp?: RTCSessionDescriptionInit;
          candidate?: RTCIceCandidateInit;
        };
        if (msg.kind === "kicked") {
          fail("Another browser took over this view.", "kicked");
          return;
        }
        if (msg.kind === "fail") {
          const stage = msg.stage === "negotiate" ? "Desktop could not negotiate the stream" : "Desktop could not start screen capture";
          fail(msg.error ? `${stage}: ${msg.error}` : `${stage}.`);
          return;
        }
        if (msg.kind === "close") {
          fail("Desktop closed the stream.");
          return;
        }
        if (msg.sdp) {
          void pc.setRemoteDescription(msg.sdp).then(
            () => {
              haveAnswer = true;
              for (const c of pendingIce.splice(0)) addIce(c);
            },
            (err: unknown) => fail(`Bad answer from desktop: ${String((err as Error)?.message || err)}`),
          );
        } else if (msg.candidate) {
          addIce(msg.candidate);
        }
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
        send({ type: "size", ...size });
      }, SIZE_DEBOUNCE_MS);
    });
    ro.observe(el);
    return () => {
      window.clearTimeout(timer);
      ro.disconnect();
    };
  }, [hwnd, attempt, send]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !hwnd || phase !== "live") return;
    return attachInput(el, send);
  }, [hwnd, phase, attempt, send]);

  useEffect(() => {
    bindRemoteSend(phase === "live" ? send : null);
    return () => bindRemoteSend(null);
  }, [phase, send]);

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
      {phase === "live" && controls.overlay && watchingUefn ? (
        <UefnStickOverlay send={send} mode={controls.mode} look={controls.look} deadzone={controls.deadzone} />
      ) : null}
    </div>
  );
}

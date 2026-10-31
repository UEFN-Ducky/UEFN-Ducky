/**
 * Viewer side of Remote View direct mode: the panel running in a phone
 * browser talks to the desktop over one RTCPeerConnection. Signaling goes
 * through a "bridge" — the site's /ducky page (postMessage) in production,
 * or the desktop's loopback HTTP API in local end-to-end tests.
 *
 * Exposes: invoke() for PanelApi calls, an event feed, blob fetches for the
 * Service Worker, stream sockets for terminal/LSP, and the video track plus
 * input channel for the window overlay.
 */
import type { AgentEvent } from "../types/panel";
import { assetBase } from "./assetBase";
import { rankVideoCodec } from "../components/remoteWindowMath";
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
  REMOTE_RESYNC,
  STREAM_PREFIX,
  compareVersions,
  newSessionId,
  rewriteLoopbackUrls,
  sendPaced,
  splitText,
  waitIceGathering,
  type BlobDone,
  type BlobHeader,
  type EventFrame,
  type RpcReply,
  type SessionDescriptionJson,
} from "./protocol";

/** `reconnecting`: the link was live and dropped (a phone that slept); it redials on its own. */
export type DirectState = "idle" | "connecting" | "live" | "reconnecting" | "failed";
export type DirectStatus = { state: DirectState; reason: string; desktopVersion: string };

export type DirectConfig = {
  /** Where signaling goes: the site page, the desktop's loopback API (tests), or —
   * in a window of this app showing another of your UEFN Duckies — this Ducky,
   * which hands the offer to that one sealed (frontend/ducky_link.py). */
  bridge: "parent" | "local" | "ducky";
  /** Local bridge only: desktop panel origin, e.g. http://127.0.0.1:4199. */
  desktopOrigin?: string;
  /** Ducky bridge only: the other UEFN Ducky's key id, and its name for the window. */
  to?: string;
  name?: string;
  ice?: RTCIceServer[];
  /** Panel bundle version (from build) for skew checks. */
  panelVersion: string;
  connectBudgetMs?: number;
};

type Bridge = {
  connect(session: string, offer: SessionDescriptionJson, ice: RTCIceServer[]): Promise<{
    answer: SessionDescriptionJson;
    desktop?: { version?: string; protocol?: number };
  }>;
  report(payload: Record<string, unknown>): void;
};

const RPC_TIMEOUT_MS = 30_000;
/** How long an asset asked for before the link is up waits for the blob channel. */
const BLOB_WAIT_MS = 30_000;
/** A download fails only after this long with no new bytes: a big video on mobile data takes minutes. */
const BLOB_IDLE_MS = 60_000;
const CONNECT_BUDGET_MS = 8_000;
const ICE_GATHER_MS = 2_500;
/** Redial backoff after a live link dropped: 1 s, 2 s, 4 s … capped. */
const REDIAL_MIN_MS = 1_000;
const REDIAL_MAX_MS = 15_000;
/** The site's Retry button (a reconnect that keeps failing). */
export const UD_DIRECT_RETRY = "ud-direct-retry";
export { REMOTE_RESYNC };
const LOOPBACK_ORIGINS = ["http://127.0.0.1:4199", "http://localhost:4199"];


type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: number };

let singleton: DirectTransport | null = null;

export function isDirectMode(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as Window & { __duckyDirect?: DirectConfig };
  if (w.__duckyDirect) return true;
  const q = new URLSearchParams(window.location.search);
  return q.get("direct") === "1" || q.get("direct") === "local" || q.get("direct") === "ducky";
}

/** A window of this app showing another of your UEFN Duckies (not this one). */
export function isRemoteDuckyWindow(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("direct") === "ducky";
}

/**
 * The other Ducky's window lives inside this app, so pywebview would hand its page
 * this Ducky's JS bridge, and code that sees one reads this PC's chats and files.
 * Here every call must go to the other Ducky, so the bridge never lands.
 */
export function hideDesktopBridge(): void {
  try {
    Object.defineProperty(window, "pywebview", { configurable: false, get: () => undefined, set: () => undefined });
  } catch {
    /* already defined: the page loaded with a bridge it can't drop */
  }
}

/** Build config from the URL / injected globals. */
export function directConfigFromLocation(panelVersion: string): DirectConfig {
  const w = window as Window & { __duckyDirect?: DirectConfig };
  if (w.__duckyDirect) return { ...w.__duckyDirect, panelVersion: w.__duckyDirect.panelVersion || panelVersion };
  const q = new URLSearchParams(window.location.search);
  if (q.get("direct") === "ducky") {
    return { bridge: "ducky", to: q.get("to") || "", name: q.get("name") || "UEFN Ducky", panelVersion };
  }
  if (q.get("direct") === "local") {
    return {
      bridge: "local",
      // `desktop=` (empty) means same-origin: the Vite dev proxy or the
      // desktop's own panel server forwards /__panel_api.
      desktopOrigin: q.has("desktop") ? q.get("desktop") || "" : "http://127.0.0.1:4199",
      panelVersion,
    };
  }
  return { bridge: "parent", panelVersion };
}

export function getDirectTransport(): DirectTransport | null {
  return singleton;
}

export function startDirectTransport(config: DirectConfig): DirectTransport {
  if (singleton) return singleton;
  singleton = new DirectTransport(config);
  // Test hook: end-to-end drivers read status and call invoke() through this.
  (window as Window & { __duckyDirectTransport?: DirectTransport }).__duckyDirectTransport = singleton;
  void singleton.connect();
  return singleton;
}

function parentBridge(): Bridge {
  const waiters = new Map<string, { resolve: (v: { answer: SessionDescriptionJson; desktop?: { version?: string; protocol?: number } }) => void; reject: (e: Error) => void }>();
  window.addEventListener("message", (ev: MessageEvent) => {
    const data = ev.data as Record<string, unknown> | null;
    if (!data || typeof data !== "object") return;
    const session = String(data.session || "");
    const w = waiters.get(session);
    if (!w) return;
    if (data.type === "ud-direct-answer" && data.answer) {
      waiters.delete(session);
      w.resolve({
        answer: data.answer as SessionDescriptionJson,
        desktop: (data.desktop as { version?: string; protocol?: number }) || undefined,
      });
    } else if (data.type === "ud-direct-error") {
      waiters.delete(session);
      w.reject(new Error(String(data.error || "signaling failed")));
    }
  });
  return {
    connect(session, offer, ice) {
      return new Promise((resolve, reject) => {
        waiters.set(session, { resolve, reject });
        window.parent.postMessage(
          { type: "ud-direct-offer", session, offer, ice, protocol: PROTOCOL_VERSION },
          "*",
        );
        window.setTimeout(() => {
          if (waiters.delete(session)) reject(new Error("signaling timed out"));
        }, 45_000);
      });
    },
    report(payload) {
      try {
        window.parent.postMessage({ type: "ud-direct-report", ...payload }, "*");
      } catch {
        /* ignore */
      }
    },
  };
}

/** The desktop's loopback API answers the offer: itself (local end-to-end runs), or
 * with `to`, another of your UEFN Duckies it relays it to sealed (`ducky_link_rtc_connect`). */
function localBridge(desktopOrigin: string, to?: string): Bridge {
  const method = to === undefined ? "direct_rtc_connect" : "ducky_link_rtc_connect";
  return {
    async connect(session, offer, ice) {
      const call: Record<string, unknown> = { session, offer, ice, protocol: PROTOCOL_VERSION };
      if (to !== undefined) call.to = to;
      const r = await fetch(`${desktopOrigin}/__panel_api/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ args: [call] }),
      });
      const json = (await r.json()) as { ok?: boolean; result?: Record<string, unknown>; error?: string };
      if (!r.ok || json.ok === false || !json.result) throw new Error(json.error || `HTTP ${r.status}`);
      const res = json.result;
      if (!res.answer) throw new Error(String(res.error || "no answer"));
      return {
        answer: res.answer as SessionDescriptionJson,
        desktop: { version: String(res.version || ""), protocol: Number(res.protocol || 0) },
      };
    },
    report() {
      /* local runs log to console only */
    },
  };
}

export class DirectTransport {
  readonly config: DirectConfig;
  private bridge: Bridge;
  private pc: RTCPeerConnection | null = null;
  private rpc: RTCDataChannel | null = null;
  private events: RTCDataChannel | null = null;
  private blob: RTCDataChannel | null = null;
  private input: RTCDataChannel | null = null;

  private videoStream: MediaStream | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private parts = new PartAssembler();
  private eventListeners = new Set<(event: AgentEvent) => void>();
  private statusListeners = new Set<(s: DirectStatus) => void>();
  private trackListeners = new Set<(s: MediaStream | null) => void>();
  private blobWaiters = new Map<
    number,
    {
      header?: BlobHeader;
      chunks: Uint8Array[];
      resolve: (r: Response) => void;
      reject: (e: Error) => void;
      /** Ask again (a fresh id) once a dropped link is back. */
      resend: () => void;
      /** Idle timer: reset whenever bytes arrive. */
      timer: number;
      alive: () => void;
    }
  >();
  private streamSockets = new Map<string, DirectStreamSocket>();
  private queuedInvokes: Array<() => void> = [];
  private queuedBlobs: Array<() => void> = [];
  private attempt = 0;
  /** Went live at least once: a drop after that redials instead of failing. */
  private wasLive = false;
  /** A connect() is in flight (offer out, waiting to connect). */
  private dialing = false;
  private redialTimer = 0;
  private redialMs = 0;
  status: DirectStatus = { state: "idle", reason: "", desktopVersion: "" };
  lastEventSeq = 0;
  /** Diagnostics for end-to-end drivers: what happened, in order, with ms since start. */
  timeline: Array<{ t: number; event: string }> = [];
  lastFailure: Record<string, unknown> | null = null;
  private t0 = 0;

  private mark(event: string) {
    this.timeline.push({ t: Math.round(performance.now() - this.t0), event });
    if (this.timeline.length > 200) this.timeline.shift();
  }

  constructor(config: DirectConfig) {
    this.config = config;
    this.bridge =
      config.bridge === "local"
        ? localBridge(config.desktopOrigin ?? "http://127.0.0.1:4199")
        : config.bridge === "ducky"
          ? // Same origin: this window is served by this Ducky's own panel server.
            localBridge("", config.to ?? "")
          : parentBridge();
    installServiceWorkerBlobBridge(this);
    this.installWakeRedial();
  }

  /** Screen back on, network back, page restored, or the site's Retry: dial now. */
  private installWakeRedial() {
    if (typeof window === "undefined") return;
    const wake = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      this.redialNow("woke up");
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    window.addEventListener("pageshow", wake);
    window.addEventListener("message", (ev: MessageEvent) => {
      if (ev.source !== window.parent || window.parent === window) return;
      const data = ev.data as { type?: string } | null;
      if (data?.type === UD_DIRECT_RETRY) this.redialNow("retry pressed", true);
    });
  }

  /** Redial unless the link is fine. `force` also redials a link that never went live. */
  redialNow(why: string, force = false): void {
    const st = this.status.state;
    const pc = this.pc;
    if (st === "live" && pc && pc.connectionState === "connected") return;
    if (!this.wasLive && !force) return;
    if (this.dialing && st !== "live") return;
    this.mark(`redial: ${why}`);
    window.clearTimeout(this.redialTimer);
    this.redialTimer = 0;
    this.redialMs = 0;
    // A live or stalled (disconnected) link is dropped before the new one dials.
    if (st === "live" || (this.pc && !this.dialing)) this.fail("Link went quiet.");
    this.retry();
  }

  /** Next backoff redial; none while the page is hidden (a wake event redials). */
  private scheduleRedial() {
    window.clearTimeout(this.redialTimer);
    this.redialTimer = 0;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    this.redialMs = this.redialMs ? Math.min(this.redialMs * 2, REDIAL_MAX_MS) : REDIAL_MIN_MS;
    this.redialTimer = window.setTimeout(() => {
      this.redialTimer = 0;
      if (this.status.state === "reconnecting" && !this.dialing) this.retry();
    }, this.redialMs);
  }

  // ── lifecycle ────────────────────────────────────────────────────────────

  private setStatus(state: DirectState, reason = "", desktopVersion = this.status.desktopVersion) {
    this.status = { state, reason, desktopVersion };
    for (const fn of this.statusListeners) fn(this.status);
    this.bridge.report({ state, reason, attempt: this.attempt });
  }

  onStatus(fn: (s: DirectStatus) => void): () => void {
    this.statusListeners.add(fn);
    fn(this.status);
    return () => this.statusListeners.delete(fn);
  }

  async connect(): Promise<void> {
    if (this.dialing || this.status.state === "live") return;
    this.dialing = true;
    window.clearTimeout(this.redialTimer);
    this.redialTimer = 0;
    this.attempt += 1;
    const resuming = this.wasLive;
    this.setStatus(resuming ? "reconnecting" : "connecting", resuming ? this.status.reason : "");
    const t0 = performance.now();
    this.t0 = t0;
    this.mark(`connect attempt ${this.attempt}`);
    const session = newSessionId();
    const ice = this.config.ice?.length ? this.config.ice : DEFAULT_ICE;
    const pc = new RTCPeerConnection({ iceServers: ice, bundlePolicy: "max-bundle", rtcpMuxPolicy: "require" });
    this.pc = pc;
    this.rpc = pc.createDataChannel(CHANNEL_RPC, { ordered: true });
    this.events = pc.createDataChannel(CHANNEL_EVENTS, { ordered: true });
    this.blob = pc.createDataChannel(CHANNEL_BLOB, { ordered: true });
    this.input = pc.createDataChannel(CHANNEL_INPUT, { ordered: true });
    this.blob.binaryType = "arraybuffer";
    const videoTx = pc.addTransceiver("video", { direction: "recvonly" });
    try {
      const caps = RTCRtpReceiver.getCapabilities?.("video");
      if (caps) videoTx.setCodecPreferences([...caps.codecs].sort((a, b) => rankVideoCodec(a) - rankVideoCodec(b)));
    } catch {
      /* Safari < 15.4 */
    }
    this.wireChannels();
    pc.ontrack = (ev) => {
      this.videoStream = ev.streams[0] ?? new MediaStream(ev.track ? [ev.track] : []);
      for (const fn of this.trackListeners) fn(this.videoStream);
    };
    pc.ondatachannel = (ev) => {
      if (ev.channel.label.startsWith(STREAM_PREFIX)) this.adoptStream(ev.channel);
    };
    pc.oniceconnectionstatechange = () => this.mark(`ice ${pc.iceConnectionState}`);
    pc.onicegatheringstatechange = () => this.mark(`gathering ${pc.iceGatheringState}`);
    pc.onsignalingstatechange = () => this.mark(`signaling ${pc.signalingState}`);
    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      this.mark(`connection ${st}`);
      if (st === "connected") {
        if (this.pc !== pc) return;
        this.dialing = false;
        this.redialMs = 0;
        this.wasLive = true;
        this.setStatus("live", "");
        // Events sent while the link was down never arrive: views reload instead.
        if (resuming) for (const fn of this.eventListeners) fn({ type: REMOTE_RESYNC } as unknown as AgentEvent);
        if (this.rpc?.readyState === "open") this.flushQueued();
        if (this.blob?.readyState === "open") this.flushBlobs();
        this.bridge.report({ connect_ms: Math.round(performance.now() - t0), candidate: "" });
        void this.reportCandidatePair();
      } else if (st === "failed" || st === "closed") {
        if (this.pc !== pc) return;
        this.fail(this.status.state === "live" ? "Peer connection dropped." : "Peer connection failed.");
      } else if (st === "disconnected" && this.status.state === "live" && this.pc === pc) {
        this.setStatus("reconnecting", "Reconnecting…");
      }
    };
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      this.mark("offer set");
      await waitIceGathering(pc, ICE_GATHER_MS);
      const local = pc.localDescription;
      if (!local) throw new Error("no local description");
      this.mark(`offer sent (${local.sdp.length} chars)`);
      const reply = await this.bridge.connect(session, { type: "offer", sdp: local.sdp }, ice);
      this.mark(`answer received (${reply.answer?.sdp?.length ?? 0} chars, desktop ${reply.desktop?.version ?? "?"})`);
      const desktopVersion = String(reply.desktop?.version || "");
      const proto = Number(reply.desktop?.protocol || PROTOCOL_VERSION);
      if (proto !== PROTOCOL_VERSION) {
        this.fail("Update the desktop app.");
        return;
      }
      if (desktopVersion && compareVersions(desktopVersion, this.config.panelVersion) < 0) {
        this.status.desktopVersion = desktopVersion;
      }
      if (pc.signalingState === "closed" || this.pc !== pc) return;
      await pc.setRemoteDescription({ type: "answer", sdp: reply.answer.sdp });
      this.mark("answer set");
      this.status.desktopVersion = desktopVersion;
      const budget = this.config.connectBudgetMs ?? CONNECT_BUDGET_MS;
      window.setTimeout(() => {
        if (this.pc === pc && pc.connectionState !== "connected") this.fail("No peer-to-peer path in time (network may block UDP).");
      }, budget);
    } catch (err) {
      if (this.pc === pc) this.fail(`Signaling failed: ${String((err as Error)?.message || err)}`);
    }
  }

  private fail(reason: string) {
    if (this.status.state === "failed") return;
    // Already torn down (a second state change for the same dead link).
    if (this.status.state === "reconnecting" && !this.pc && !this.dialing) return;
    this.dialing = false;
    this.setStatus(this.wasLive ? "reconnecting" : "failed", reason);
    const pc = this.pc;
    if (pc) {
      // Keep enough to diagnose a failed link from the console alone.
      const summary = { reason, ice: pc.iceConnectionState, conn: pc.connectionState, sig: pc.signalingState, gather: pc.iceGatheringState };
      void pc
        .getStats()
        .then((st) => {
          const local: string[] = [];
          const remote: string[] = [];
          const pairs: string[] = [];
          st.forEach((r) => {
            const row = r as unknown as Record<string, unknown>;
            if (row.type === "local-candidate") local.push(`${row.candidateType} ${row.protocol} ${row.address ?? ""}:${row.port}`);
            if (row.type === "remote-candidate") remote.push(`${row.candidateType} ${row.protocol} ${row.address ?? ""}:${row.port}`);
            if (row.type === "candidate-pair") pairs.push(`${row.state}${row.nominated ? " nominated" : ""}`);
          });
          this.lastFailure = { ...summary, local, remote, pairs };
          console.error("[remote-direct] viewer failed", JSON.stringify(this.lastFailure));
        })
        .catch(() => {
          this.lastFailure = summary;
          console.error("[remote-direct] viewer failed", JSON.stringify(summary));
        });
    } else {
      this.lastFailure = { reason };
    }
    this.mark(`failed: ${reason}`);
    this.teardown();
    for (const p of this.pending.values()) {
      window.clearTimeout(p.timer);
      p.reject(new Error(reason));
    }
    this.pending.clear();
    // Pictures and videos mid-download: ask again once the redial is up (nothing else would,
    // so they stayed broken after a phone slept or switched network); give up when not redialing.
    const inflight = [...this.blobWaiters.values()];
    this.blobWaiters.clear();
    for (const w of inflight) {
      window.clearTimeout(w.timer);
      if (this.wasLive) this.queueBlob(w.resend, w.reject);
      else w.reject(new Error(reason));
    }
    if (this.wasLive) this.scheduleRedial();
  }

  private teardown() {
    const pc = this.pc;
    this.pc = null;
    for (const s of this.streamSockets.values()) s.closeFromPeer();
    this.streamSockets.clear();
    for (const ch of [this.rpc, this.events, this.blob, this.input]) {
      try {
        ch?.close();
      } catch {
        /* ignore */
      }
    }
    try {
      pc?.close();
    } catch {
      /* ignore */
    }
    this.videoStream = null;
    for (const fn of this.trackListeners) fn(null);
  }

  retry(): void {
    window.clearTimeout(this.redialTimer);
    this.redialTimer = 0;
    if (this.status.state === "failed") this.status = { ...this.status, state: "idle" };
    void this.connect();
  }

  /** The link froze (nothing arriving) or sat disconnected: drop it and dial again. */
  restart(reason: string): void {
    if (this.status.state === "idle") return;
    this.mark(`restart: ${reason}`);
    if (this.status.state === "live" || this.dialing) this.fail(reason);
    this.retry();
  }

  private flushQueued() {
    const q = this.queuedInvokes.splice(0);
    for (const fn of q) fn();
  }

  // ── rpc ──────────────────────────────────────────────────────────────────

  invoke(method: string, args: unknown[]): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const send = () => {
        const ch = this.rpc;
        if (!ch || ch.readyState !== "open") {
          // Connected but the rpc channel is still opening: wait for it.
          if (ch && ch.readyState === "connecting" && this.status.state !== "failed") {
            this.queuedInvokes.push(send);
            return;
          }
          reject(new Error("desktop not connected"));
          return;
        }
        const id = this.nextId++;
        const timer = window.setTimeout(() => {
          this.pending.delete(id);
          this.parts.drop(id);
          reject(new Error("timeout"));
        }, RPC_TIMEOUT_MS);
        this.pending.set(id, {
          resolve: (v) => resolve(rewriteLoopbackUrls(v, LOOPBACK_ORIGINS, assetBase())),
          reject,
          timer,
        });
        const text = JSON.stringify({ id, method, args });
        const pieces = splitText(text);
        void sendPaced(ch, pieces.length === 1 ? [text] : pieces.map((data, part) => JSON.stringify({ id, part, of: pieces.length, data })));
      };
      if (this.status.state === "live" && this.rpc?.readyState === "open") send();
      // A redial in progress (or waiting on a wake): the call goes once the link is back.
      else if (this.status.state === "failed") reject(new Error(this.status.reason || "desktop not connected"));
      else {
        this.queuedInvokes.push(send);
        window.setTimeout(() => {
          const i = this.queuedInvokes.indexOf(send);
          if (i >= 0) {
            this.queuedInvokes.splice(i, 1);
            reject(new Error("desktop not connected"));
          }
        }, RPC_TIMEOUT_MS);
      }
    });
  }

  private onRpcMessage(raw: string) {
    let msg: RpcReply;
    try {
      msg = JSON.parse(raw) as RpcReply;
    } catch {
      return;
    }
    if ("part" in msg && typeof msg.part === "number") {
      const full = this.parts.push(msg.id, msg.part, msg.of, msg.data);
      if (full === null) return;
      try {
        msg = JSON.parse(full) as RpcReply;
      } catch {
        return;
      }
    }
    const p = this.pending.get(msg.id);
    if (!p) return;
    this.pending.delete(msg.id);
    window.clearTimeout(p.timer);
    if ("ok" in msg && msg.ok === false) p.reject(new Error(String((msg as { error?: string }).error || "rpc failed")));
    else p.resolve((msg as { result?: unknown }).result);
  }

  // ── events ───────────────────────────────────────────────────────────────

  onEvent(fn: (event: AgentEvent) => void): () => void {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  }

  private eventParts = new PartAssembler();

  private onEventMessage(raw: string) {
    let frame: EventFrame;
    try {
      const parsed = JSON.parse(raw) as EventFrame & { part?: number; of?: number; data?: string };
      if (typeof parsed.part === "number" && typeof parsed.of === "number") {
        const full = this.eventParts.push(parsed.seq, parsed.part, parsed.of, parsed.data || "");
        if (full === null) return;
        frame = JSON.parse(full) as EventFrame;
      } else frame = parsed;
    } catch {
      return;
    }
    if (typeof frame.seq === "number") this.lastEventSeq = frame.seq;
    const event = rewriteLoopbackUrls(frame.event, LOOPBACK_ORIGINS, assetBase()) as unknown as AgentEvent;
    for (const fn of this.eventListeners) fn(event);
  }

  // ── blob ─────────────────────────────────────────────────────────────────

  fetchBlob(path: string, etag?: string): Promise<Response> {
    return new Promise((resolve, reject) => {
      const send = () => {
        const ch = this.blob;
        if (!ch || ch.readyState !== "open") {
          reject(new Error("desktop not connected"));
          return;
        }
        const id = this.nextId++;
        const waiter = { chunks: [] as Uint8Array[], resolve, reject, resend: send, timer: 0, alive: () => {} };
        waiter.alive = () => {
          window.clearTimeout(waiter.timer);
          waiter.timer = window.setTimeout(() => {
            if (this.blobWaiters.get(id) !== waiter) return;
            this.blobWaiters.delete(id);
            reject(new Error("blob timeout"));
          }, BLOB_IDLE_MS);
        };
        waiter.alive();
        this.blobWaiters.set(id, waiter);
        ch.send(JSON.stringify({ id, get: path, etag }));
      };
      if (this.blob?.readyState === "open") {
        send();
        return;
      }
      // Pictures asked for while the link is still opening (a phone's first seconds,
      // "Connecting to …") wait for the blob channel: answered at once, the image
      // stayed broken, since nothing asks for it again once the link is up.
      this.queueBlob(send, reject);
    });
  }

  /** Hold a download until the blob channel opens; give up after BLOB_WAIT_MS. */
  private queueBlob(send: () => void, reject: (e: Error) => void) {
    this.queuedBlobs.push(send);
    window.setTimeout(() => {
      const i = this.queuedBlobs.indexOf(send);
      if (i >= 0) {
        this.queuedBlobs.splice(i, 1);
        reject(new Error("desktop not connected"));
      }
    }, BLOB_WAIT_MS);
  }

  private flushBlobs() {
    const q = this.queuedBlobs.splice(0);
    for (const fn of q) fn();
  }

  private onBlobMessage(data: string | ArrayBuffer) {
    if (typeof data === "string") {
      let msg: BlobHeader | BlobDone;
      try {
        msg = JSON.parse(data) as BlobHeader | BlobDone;
      } catch {
        return;
      }
      const w = this.blobWaiters.get(msg.id);
      if (!w) return;
      window.clearTimeout(w.timer);
      if ("done" in msg) {
        this.blobWaiters.delete(msg.id);
        const h = w.header;
        const body = h && h.status === 304 ? null : concat(w.chunks);
        const headers: Record<string, string> = {};
        if (h?.type) headers["Content-Type"] = h.type;
        if (h?.etag) headers.ETag = h.etag;
        w.resolve(new Response(body, { status: h?.status ?? 200, headers }));
        return;
      }
      w.header = msg;
      if (msg.status === 304 || msg.status >= 400) {
        this.blobWaiters.delete(msg.id);
        w.resolve(new Response(null, { status: msg.status, headers: msg.etag ? { ETag: msg.etag } : {} }));
        return;
      }
      w.alive();
      return;
    }
    // Binary chunk: 4-byte big-endian id prefix, then bytes.
    const view = new DataView(data);
    const id = view.getUint32(0);
    const w = this.blobWaiters.get(id);
    if (!w) return;
    w.chunks.push(new Uint8Array(data, 4));
    w.alive();
  }

  // ── input + video (window overlay) ───────────────────────────────────────

  sendInput(payload: Record<string, unknown>): boolean {
    const ch = this.input;
    if (!ch || ch.readyState !== "open") return false;
    ch.send(JSON.stringify(payload));
    return true;
  }

  onVideo(fn: (stream: MediaStream | null) => void): () => void {
    this.trackListeners.add(fn);
    fn(this.videoStream);
    return () => this.trackListeners.delete(fn);
  }

  get peer(): RTCPeerConnection | null {
    return this.pc;
  }

  // ── streams (terminal / lsp) ─────────────────────────────────────────────

  openStream(kind: "terminal" | "lsp", url: string): DirectStreamSocket {
    const id = `${STREAM_PREFIX}${newSessionId().slice(0, 12)}`;
    const sock = new DirectStreamSocket(id);
    this.streamSockets.set(id, sock);
    void this.invoke("__stream_open", [{ id, kind, url }]).then(
      () => {},
      (err) => sock.failOpen(String((err as Error)?.message || err)),
    );
    return sock;
  }

  private adoptStream(ch: RTCDataChannel) {
    const sock = this.streamSockets.get(ch.label);
    if (!sock) {
      try {
        ch.close();
      } catch {
        /* ignore */
      }
      return;
    }
    sock.attach(ch, () => this.streamSockets.delete(ch.label));
  }

  // ── wiring ───────────────────────────────────────────────────────────────

  private wireChannels() {
    if (this.rpc) {
      this.rpc.onmessage = (ev) => typeof ev.data === "string" && this.onRpcMessage(ev.data);
      // Calls made during boot wait here; the DataChannel opens a beat after
      // the peer connection reports "connected".
      this.rpc.onopen = () => {
        this.mark("rpc open");
        this.flushQueued();
      };
    }
    if (this.events) this.events.onmessage = (ev) => typeof ev.data === "string" && this.onEventMessage(ev.data);
    if (this.blob) {
      this.blob.onmessage = (ev) => this.onBlobMessage(ev.data as string | ArrayBuffer);
      this.blob.onopen = () => {
        this.mark("blob open");
        this.flushBlobs();
      };
    }
  }

  private async reportCandidatePair() {
    const pc = this.pc;
    if (!pc) return;
    try {
      const stats = await pc.getStats();
      let kind = "";
      stats.forEach((row) => {
        const r = row as Record<string, unknown>;
        if (r.type === "candidate-pair" && (r.nominated || r.state === "succeeded")) {
          const local = stats.get(String(r.localCandidateId)) as Record<string, unknown> | undefined;
          if (local) kind = String(local.candidateType || "");
        }
      });
      this.bridge.report({ candidate: kind });
    } catch {
      /* ignore */
    }
  }
}

/** WebSocket-shaped wrapper over a `stream:<id>` DataChannel. */
export class DirectStreamSocket {
  readonly label: string;
  private ch: RTCDataChannel | null = null;
  private queue: Array<string | ArrayBuffer> = [];
  private onDetach: (() => void) | null = null;
  readyState: 0 | 1 | 2 | 3 = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string | ArrayBuffer }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  binaryType: "arraybuffer" | "blob" = "arraybuffer";

  constructor(label: string) {
    this.label = label;
  }

  attach(ch: RTCDataChannel, onDetach: () => void) {
    this.ch = ch;
    this.onDetach = onDetach;
    ch.binaryType = "arraybuffer";
    const opened = () => {
      this.readyState = 1;
      for (const m of this.queue.splice(0)) ch.send(m as string);
      this.onopen?.();
    };
    if (ch.readyState === "open") opened();
    else ch.onopen = opened;
    ch.onmessage = (ev) => this.onmessage?.({ data: ev.data as string | ArrayBuffer });
    ch.onclose = () => this.closeFromPeer();
    ch.onerror = (ev) => this.onerror?.(ev);
  }

  send(data: string | ArrayBuffer | Uint8Array) {
    const payload = data instanceof Uint8Array ? (data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer) : data;
    if (this.ch && this.ch.readyState === "open") {
      if (typeof payload === "string") {
        for (const piece of splitText(payload, MAX_CHUNK_BYTES)) this.ch.send(piece);
      } else this.ch.send(payload);
      return;
    }
    this.queue.push(payload);
  }

  close(code = 1000, reason = "") {
    if (this.readyState >= 2) return;
    this.readyState = 2;
    try {
      this.ch?.close();
    } catch {
      /* ignore */
    }
    this.readyState = 3;
    this.onDetach?.();
    this.onclose?.({ code, reason });
  }

  closeFromPeer() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onDetach?.();
    this.onclose?.({ code: 1006, reason: "peer closed" });
  }

  failOpen(reason: string) {
    this.readyState = 3;
    this.onerror?.(new Error(reason));
    this.onclose?.({ code: 1006, reason });
  }
}

function concat(chunks: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const c of chunks) n += c.byteLength;
  const out = new Uint8Array(n);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.byteLength;
  }
  return out;
}

/** How long the first open waits for the Service Worker before drawing anyway. */
export const SW_CONTROL_WAIT_MS = 3_000;

/**
 * Resolves once the Service Worker controls this page (or after `maxMs`). On a first
 * open, or right after the worker updated, the page starts uncontrolled: pictures it
 * asks for then (plugin icons, custom duckies) go to the site, 404, and stay broken,
 * since nothing asks for them again. sw.js claims its clients on activate.
 */
export function serviceWorkerControl(maxMs = SW_CONTROL_WAIT_MS): Promise<void> {
  const sw = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
  if (!sw || sw.controller) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      window.clearTimeout(timer);
      sw.removeEventListener("controllerchange", done);
      resolve();
    };
    const timer = window.setTimeout(done, maxMs);
    sw.addEventListener("controllerchange", done);
  });
}

/**
 * The Service Worker (public/sw.js) forwards asset requests to the page over
 * a MessageChannel; the page answers with the bytes from the blob channel.
 */
function installServiceWorkerBlobBridge(transport: DirectTransport) {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener("message", (ev: MessageEvent) => {
    const data = ev.data as { type?: string; path?: string; etag?: string } | null;
    if (!data || data.type !== "ud-blob-fetch" || !data.path) return;
    const port = ev.ports[0];
    if (!port) return;
    transport.fetchBlob(data.path, data.etag).then(
      async (res) => {
        const buf = res.status === 304 || res.status >= 400 ? null : await res.arrayBuffer();
        port.postMessage(
          { status: res.status, type: res.headers.get("Content-Type") || "", etag: res.headers.get("ETag") || "", body: buf },
          buf ? [buf] : [],
        );
      },
      (err) => port.postMessage({ status: 502, error: String((err as Error)?.message || err) }),
    );
  });
  // Register relative to the panel document so the worker's scope is exactly
  // the directory the panel was served from — no Service-Worker-Allowed header
  // and no claim on the rest of the site's origin.
  try {
    const swUrl = new URL("sw.js", document.baseURI).href;
    navigator.serviceWorker.register(swUrl, { scope: assetBase() }).catch(() => {
      /* not fatal: assets just won't resolve until the SW is available */
    });
  } catch {
    /* ignore */
  }
}

export { BLOB_LOW_WATER_BYTES };

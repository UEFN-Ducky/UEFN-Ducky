/**
 * Desktop-side screen capture shared by the tunnel sender (RemoteWindowSender)
 * and the direct peer (directPeer). One getDisplayMedia track for the app's
 * lifetime, reference-counted; a cropped clone per consumer, cropped in a
 * Worker so the panel UI thread never stalls the frame pump.
 */
import { getApi } from "../hooks/usePanelApi";
import type { WindowBox } from "../types/panel";
import { rankVideoCodec } from "../components/remoteWindowMath";

export const MAX_BITRATE = 20_000_000;
export const MAX_FPS = 60;
const BOX_POLL_MS = 250;
/** Drop the capture (and its "sharing your screen" bar) once nobody watched for this long. */
const IDLE_RELEASE_MS = 5000;

type CropCtx = { onmessage: ((ev: { data: CropMessage }) => void) | null };
type CropMessage =
  | { type: "box"; box: WindowBox | null }
  | { type: "start"; readable: ReadableStream<VideoFrame>; writable: WritableStream<VideoFrame> };

let screenPromise: Promise<MediaStreamTrack> | null = null;
let holders = 0;
let idleTimer = 0;
const endedListeners = new Set<() => void>();

export function onScreenEnded(fn: () => void): () => void {
  endedListeners.add(fn);
  return () => endedListeners.delete(fn);
}

function releaseScreenWhenIdle() {
  window.clearTimeout(idleTimer);
  idleTimer = window.setTimeout(() => {
    if (holders > 0 || !screenPromise) return;
    const p = screenPromise;
    screenPromise = null;
    void p.then((track) => track.stop()).catch(() => {});
  }, IDLE_RELEASE_MS);
}

/** Acquire the shared screen track. Pair with releaseScreenTrack(). */
export function acquireScreenTrack(): Promise<MediaStreamTrack> {
  holders += 1;
  window.clearTimeout(idleTimer);
  return screenTrack().catch((err) => {
    holders = Math.max(0, holders - 1);
    throw err;
  });
}

export function releaseScreenTrack(): void {
  holders = Math.max(0, holders - 1);
  if (holders === 0) releaseScreenWhenIdle();
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
    video: { frameRate: { ideal: MAX_FPS, max: MAX_FPS }, displaySurface: "monitor" },
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
      for (const fn of endedListeners) fn();
    });
    return track;
  });
  screenPromise.catch(() => {
    screenPromise = null;
  });
  return screenPromise;
}

/**
 * Crop pump. Self-contained on purpose: it is stringified into a Worker (no
 * closure access) and also runs on the main thread as a fallback. Frames are
 * re-wrapped with a visibleRect (metadata only) and dropped instead of queued
 * when the encoder is behind — latency over completeness.
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
          const full = r.x === 0 && r.y === 0 && r.width === value.displayWidth && r.height === value.displayHeight;
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
    return { post: (m) => worker.postMessage(m), stop: () => worker.terminate() };
  } catch {
    const ctx: CropCtx = { onmessage: null };
    cropPump(ctx);
    ctx.onmessage?.({ data: { type: "start", readable, writable } });
    return { post: (m) => ctx.onmessage?.({ data: m }), stop: () => {} };
  }
}

/** A clone of the screen track cropped to `hwnd`, updated every 250 ms. Stops on abort. */
export async function croppedTrack(screen: MediaStreamTrack, hwnd: string, abort: AbortSignal): Promise<MediaStreamTrack> {
  const Processor = (
    window as unknown as {
      MediaStreamTrackProcessor?: new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<VideoFrame> };
    }
  ).MediaStreamTrackProcessor;
  const Generator = (
    window as unknown as {
      MediaStreamTrackGenerator?: new (init: { kind: "video" }) => MediaStreamTrack & { writable: WritableStream<VideoFrame> };
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

export function preferVideoCodecs(pc: RTCPeerConnection) {
  const caps = RTCRtpSender.getCapabilities?.("video");
  if (!caps) return;
  const ordered = [...caps.codecs].sort((a, b) => rankVideoCodec(a) - rankVideoCodec(b));
  for (const t of pc.getTransceivers()) {
    if (t.receiver.track?.kind !== "video") continue;
    try {
      t.setCodecPreferences(ordered);
    } catch {
      /* ignore */
    }
  }
}

export async function tuneSender(sender: RTCRtpSender) {
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

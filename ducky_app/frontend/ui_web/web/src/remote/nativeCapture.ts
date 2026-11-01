/**
 * Prompt-free capture of a window or display on any monitor: Ducky's Python grabs the pixels
 * (native_capture_bridge.py, the way Chrome Remote Desktop's native host does) into a WebView2
 * SharedBuffer, and each "native-frame" message becomes a VideoFrame on a generated track.
 * getUserMedia's whole-desktop source raised Chromium's "Screen Sharing Request" box and
 * getDisplayMedia gives one monitor, so this is the first choice; remoteCapture falls back.
 */
import { getApi } from "../hooks/usePanelApi";

type Slots = { buf: ArrayBuffer; w: number; h: number };
type FrameMsg = { type: "native-frame"; view: string; seq: number; slot: number; w: number; h: number };
type WebViewHost = {
  addEventListener(type: string, fn: (ev: { data?: unknown; additionalData?: unknown; getBuffer?: () => ArrayBuffer }) => void): void;
  releaseBuffer?(buf: ArrayBuffer): void;
};
type Generator = MediaStreamTrack & { writable: WritableStream<VideoFrame> };

/** No first frame this long after start: the native path isn't working here, fall back. */
export const FIRST_FRAME_MS = 3_000;
const FRAME_US = 1_000_000 / 30;

const slots = new Map<string, Slots>();
const sinks = new Map<string, (m: FrameMsg) => void>();
let installedOn: WebViewHost | null = null;

function host(): WebViewHost | null {
  return (window as Window & { chrome?: { webview?: WebViewHost } }).chrome?.webview ?? null;
}

function install(wv: WebViewHost) {
  if (installedOn === wv) return;
  installedOn = wv;
  wv.addEventListener("sharedbufferreceived", (ev) => {
    const meta = ev.additionalData as { view?: string; w?: number; h?: number } | undefined;
    if (!meta?.view || !ev.getBuffer) return;
    const old = slots.get(meta.view);
    slots.set(meta.view, { buf: ev.getBuffer(), w: meta.w || 0, h: meta.h || 0 });
    if (old) wv.releaseBuffer?.(old.buf);
  });
  wv.addEventListener("message", (ev) => {
    const m = ev.data as FrameMsg | undefined;
    if (m?.type === "native-frame") sinks.get(m.view)?.(m);
  });
}

/** True when this page can take native frames (the desktop app's WebView2, WebCodecs). */
export function nativeCaptureAvailable(): boolean {
  const w = window as unknown as Record<string, unknown>;
  return !!(host() && w.MediaStreamTrackGenerator && w.VideoFrame && getApi()?.native_capture_start);
}

/** A track of `view` (hwnd, "monitor:N", "desktop"), already its own size: no crop needed. */
export async function nativeTrack(view: string, abort: AbortSignal, firstFrameMs = FIRST_FRAME_MS): Promise<MediaStreamTrack> {
  const wv = host();
  const api = getApi();
  if (!wv || !api?.native_capture_start || !nativeCaptureAvailable()) throw new Error("no native capture");
  install(wv);
  const Gen = (window as unknown as { MediaStreamTrackGenerator: new (i: { kind: "video" }) => Generator }).MediaStreamTrackGenerator;
  const generator = new Gen({ kind: "video" });
  const writer = generator.writable.getWriter();
  let busy = false;
  let gotFirst: () => void = () => {};
  const first = new Promise<void>((resolve) => (gotFirst = resolve));
  sinks.set(view, (m) => {
    const s = slots.get(view);
    const size = m.w * m.h * 4;
    if (!s || s.w !== m.w || s.h !== m.h || busy || (writer.desiredSize ?? 1) < 0) return; // drop, never queue
    let frame: VideoFrame;
    try {
      frame = new VideoFrame(new Uint8Array(s.buf, m.slot * size, size), {
        format: "BGRX",
        codedWidth: m.w,
        codedHeight: m.h,
        timestamp: Math.round(m.seq * FRAME_US),
      });
    } catch {
      return; // a buffer replaced mid-frame
    }
    busy = true;
    void writer
      .write(frame)
      .catch(() => {})
      .finally(() => {
        frame.close();
        busy = false;
      });
    gotFirst();
  });
  const stop = () => {
    if (sinks.get(view)) sinks.delete(view);
    void Promise.resolve(api.native_capture_stop?.(view)).catch(() => {});
    try {
      generator.stop();
    } catch {
      /* ignore */
    }
  };
  abort.addEventListener("abort", stop);
  try {
    const out = await api.native_capture_start(view, 30);
    if (!out?.ok) throw new Error(out?.error || "native capture refused");
    let timer = 0;
    await Promise.race([
      first,
      new Promise((_r, reject) => (timer = window.setTimeout(() => reject(new Error("no native frames")), firstFrameMs))),
    ]).finally(() => window.clearTimeout(timer));
    return generator;
  } catch (err) {
    abort.removeEventListener("abort", stop);
    stop();
    throw err;
  }
}

export function resetNativeCaptureForTest() {
  slots.clear();
  sinks.clear();
  installedOn = null;
}

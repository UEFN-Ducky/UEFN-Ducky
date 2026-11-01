// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

type Api = {
  native_capture_start?: (view: string, fps: number) => Promise<{ ok: boolean; error?: string }>;
  native_capture_stop?: (view: string) => Promise<boolean>;
};
const api: Api = {};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));

import { nativeTrack, resetNativeCaptureForTest } from "./nativeCapture";
import { watchTrack } from "./remoteCapture";

type Listener = (ev: Record<string, unknown>) => void;
let listeners: Record<string, Listener[]>;
let written: { format: string; w: number; h: number; first: number }[];
let stopped: number;

function emit(type: string, ev: Record<string, unknown>) {
  for (const fn of listeners[type] || []) fn(ev);
}

beforeEach(() => {
  listeners = {};
  written = [];
  stopped = 0;
  resetNativeCaptureForTest();
  (window as unknown as { chrome: unknown }).chrome = {
    webview: { addEventListener: (t: string, fn: Listener) => (listeners[t] ||= []).push(fn), releaseBuffer: () => {} },
  };
  vi.stubGlobal(
    "VideoFrame",
    class {
      constructor(
        public data: Uint8Array,
        public init: { format: string; codedWidth: number; codedHeight: number },
      ) {}
      close() {}
    },
  );
  vi.stubGlobal(
    "MediaStreamTrackGenerator",
    class {
      kind = "video";
      writable = {
        getWriter: () => ({
          desiredSize: 1,
          write: async (f: { data: Uint8Array; init: { format: string; codedWidth: number; codedHeight: number } }) => {
            written.push({ format: f.init.format, w: f.init.codedWidth, h: f.init.codedHeight, first: f.data[0] });
          },
        }),
      };
      stop() {
        stopped += 1;
      }
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete api.native_capture_start;
  delete api.native_capture_stop;
});

function startOk(stops: string[]) {
  api.native_capture_start = async (view) => {
    // Python posts the buffer, then the first frame in slot 1.
    const buf = new ArrayBuffer(2 * 4 * 2 * 4);
    new Uint8Array(buf)[4 * 2 * 4] = 9;
    queueMicrotask(() => {
      emit("sharedbufferreceived", { additionalData: { view, w: 4, h: 2, slots: 2 }, getBuffer: () => buf });
      emit("message", { data: { type: "native-frame", view, seq: 1, slot: 1, w: 4, h: 2 } });
    });
    return { ok: true };
  };
  api.native_capture_stop = async (view) => {
    stops.push(view);
    return true;
  };
}

it("turns each native-frame into a BGRX VideoFrame of its slot and stops on abort", async () => {
  const stops: string[] = [];
  startOk(stops);
  const abort = new AbortController();
  const track = await nativeTrack("monitor:1", abort.signal);
  expect(track.kind).toBe("video");
  await vi.waitFor(() => expect(written).toEqual([{ format: "BGRX", w: 4, h: 2, first: 9 }]));
  abort.abort();
  expect(stops).toEqual(["monitor:1"]);
  expect(stopped).toBe(1);
});

it("rejects and cleans up when Python refuses or no frame ever comes", async () => {
  const stops: string[] = [];
  api.native_capture_start = async () => ({ ok: false, error: "no main WebView2" });
  api.native_capture_stop = async (v) => (stops.push(v), true);
  await expect(nativeTrack("123", new AbortController().signal)).rejects.toThrow("no main WebView2");
  api.native_capture_start = async () => ({ ok: true });
  await expect(nativeTrack("123", new AbortController().signal, 20)).rejects.toThrow("no native frames");
  expect(stops).toEqual(["123", "123"]);
});

it("watchTrack uses the native capture first, with no screen pick", async () => {
  startOk([]);
  const getDisplayMedia = vi.fn();
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getDisplayMedia } });
  const track = await watchTrack("monitor:1", new AbortController().signal);
  expect(track.kind).toBe("video");
  expect(getDisplayMedia).not.toHaveBeenCalled();
});

it("watchTrack falls back to the screen pick when native capture fails", async () => {
  api.native_capture_start = async () => ({ ok: false, error: "x" });
  api.native_capture_stop = async () => true;
  const getDisplayMedia = vi.fn().mockRejectedValue(new Error("picked"));
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getDisplayMedia } });
  vi.spyOn(console, "warn").mockImplementation(() => {});
  await expect(watchTrack("123", new AbortController().signal)).rejects.toThrow("picked");
  expect(getDisplayMedia).toHaveBeenCalled();
});

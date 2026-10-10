// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ report_ui_perf: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));

type ObserverCallback = (list: { getEntries: () => Array<{ name: string; duration: number }> }) => void;

class FakeObserver {
  static supportedEntryTypes = ["longtask", "long-animation-frame"];
  static instances: FakeObserver[] = [];
  observed: unknown[] = [];
  constructor(readonly callback: ObserverCallback) {
    FakeObserver.instances.push(this);
  }
  observe(options: unknown) {
    this.observed.push(options);
  }
  disconnect() {}
}

let rafCalls = 0;
let queued: FrameRequestCallback[] = [];

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  rafCalls = 0;
  queued = [];
  FakeObserver.instances = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafCalls += 1;
    queued.push(cb);
    return rafCalls;
  });
  vi.stubGlobal("PerformanceObserver", FakeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  api.report_ui_perf.mockReset();
});

function runFrames(count: number) {
  for (let i = 0; i < count; i++) {
    const batch = queued;
    queued = [];
    for (const cb of batch) cb(i * 16);
  }
}

it("asks for no animation frames while the page sits idle", async () => {
  const { installPerfMonitor } = await import("./perfMonitor");
  installPerfMonitor();
  runFrames(600);
  expect(rafCalls).toBe(0);
});

it("still reports a stalled frame to the backend", async () => {
  const { installPerfMonitor } = await import("./perfMonitor");
  installPerfMonitor();
  const observer = FakeObserver.instances[0];
  expect(observer.observed).toEqual([{ type: "long-animation-frame", buffered: false }]);

  observer.callback({ getEntries: () => [
    { name: "long-animation-frame", duration: 320 },
    { name: "long-animation-frame", duration: 20 },
  ] });
  await vi.advanceTimersByTimeAsync(5000);

  expect(api.report_ui_perf).toHaveBeenCalledTimes(1);
  expect(api.report_ui_perf.mock.calls[0][0]).toEqual([
    { kind: "ui_stall", name: "long-animation-frame", duration_ms: 320, peak_pending: 0 },
  ]);
});

it("falls back to long tasks where long animation frames are not reported", async () => {
  FakeObserver.supportedEntryTypes = ["longtask"];
  try {
    const { installPerfMonitor } = await import("./perfMonitor");
    installPerfMonitor();
    expect(FakeObserver.instances[0].observed).toEqual([{ type: "longtask", buffered: false }]);
  } finally {
    FakeObserver.supportedEntryTypes = ["longtask", "long-animation-frame"];
  }
});

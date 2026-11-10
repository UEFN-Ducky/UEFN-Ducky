// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ report_ui_perf: vi.fn(), available: true }));
vi.mock("./usePanelApi", () => ({ getApi: () => api.available ? api : undefined }));

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
  api.available = true;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafCalls += 1;
    queued.push(cb);
    return rafCalls;
  });
  vi.stubGlobal("PerformanceObserver", FakeObserver);
});

afterEach(() => {
  vi.clearAllTimers();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  api.report_ui_perf.mockReset();
});

it("summarizes a queue burst without evicting an earlier stall", async () => {
  const { installPerfMonitor, notePendingDepth } = await import("./perfMonitor");
  installPerfMonitor();
  FakeObserver.instances[0].callback({ getEntries: () => [{ name: "longtask", duration: 320 }] });
  for (let depth = 1; depth <= 10_000; depth++) notePendingDepth(depth);
  await vi.advanceTimersByTimeAsync(5000);
  const batch = api.report_ui_perf.mock.calls[0][0];
  expect(batch).toHaveLength(2);
  expect(batch[0]).toMatchObject({ kind: "ui_stall", duration_ms: 320 });
  expect(batch[1]).toMatchObject({ name: "pending_events", samples: 9951, peak_pending: 10_000, pending_depth: 10_000 });

  notePendingDepth(60);
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.report_ui_perf.mock.calls[1][0]).toEqual([
    { kind: "ui_frame", name: "pending_events", duration_ms: 0, samples: 1, peak_pending: 60, pending_depth: 60 },
  ]);
});

it("summarizes slow deliveries while keeping their peak, count and total work", async () => {
  const { installPerfMonitor, noteFrameDelivery, notePendingDepth } = await import("./perfMonitor");
  installPerfMonitor();
  notePendingDepth(90);
  for (let i = 0; i < 500; i++) noteFrameDelivery(20, 2, 60);
  noteFrameDelivery(45, 3, 0);
  await vi.advanceTimersByTimeAsync(5000);
  const batch = api.report_ui_perf.mock.calls[0][0];
  expect(batch).toHaveLength(2);
  expect(batch.find((row: { name: string }) => row.name === "event_delivery")).toMatchObject({
    samples: 501, duration_ms: 45, total_duration_ms: 10045, delivered: 1003, pending_depth: 0, peak_pending: 90,
  });
  noteFrameDelivery(5, 2, 80);
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.report_ui_perf.mock.calls[1][0][0]).toMatchObject({
    name: "event_delivery", samples: 1, pending_depth: 80, peak_pending: 80,
  });
});

it("keeps summaries bounded while the API is unavailable and sends them when it returns", async () => {
  const { installPerfMonitor, notePendingDepth } = await import("./perfMonitor");
  installPerfMonitor();
  api.available = false;
  for (let i = 0; i < 20; i++) {
    for (let depth = 50; depth <= 290; depth++) notePendingDepth(depth);
    await vi.advanceTimersByTimeAsync(5000);
  }
  expect(api.report_ui_perf).not.toHaveBeenCalled();
  api.available = true;
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.report_ui_perf.mock.calls[0][0]).toEqual([
    { kind: "ui_frame", name: "pending_events", duration_ms: 0, samples: 4820, peak_pending: 290, pending_depth: 290 },
  ]);
});

it("keeps IPC diagnostic batches within 200 entries even with many stalls", async () => {
  const { installPerfMonitor, notePendingDepth, noteFrameDelivery } = await import("./perfMonitor");
  installPerfMonitor();
  FakeObserver.instances[0].callback({ getEntries: () => Array.from({ length: 300 }, () => ({ name: "longtask", duration: 320 })) });
  notePendingDepth(100);
  noteFrameDelivery(20, 2, 60);
  await vi.advanceTimersByTimeAsync(5000);
  const batch = api.report_ui_perf.mock.calls[0][0];
  expect(batch).toHaveLength(200);
  expect(batch.filter((entry: { kind: string }) => entry.kind === "ui_stall")).toHaveLength(198);
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

it("names the slowest script of a long animation frame", async () => {
  const { installPerfMonitor } = await import("./perfMonitor");
  installPerfMonitor();
  const entry = {
    name: "long-animation-frame",
    duration: 180,
    scripts: [
      { duration: 20, invoker: "TimerHandler:setTimeout", sourceFunctionName: "tick", sourceURL: "http://127.0.0.1/assets/a.js" },
      { duration: 150, invoker: "FrameRequestCallback", sourceFunctionName: "deliver", sourceURL: "http://127.0.0.1/assets/index-abc.js?v=1" },
    ],
  };
  FakeObserver.instances[0].callback({ getEntries: () => [entry] });
  await vi.advanceTimersByTimeAsync(5000);
  expect(api.report_ui_perf.mock.calls[0][0][0]).toMatchObject({
    name: "long-animation-frame", duration_ms: 180, source: "FrameRequestCallback deliver @index-abc.js",
  });
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

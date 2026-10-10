/**
 * Lightweight UI-thread stall monitor for the Ducky panel.
 * Reports long animation frames (or long tasks) to Python via PanelApi.report_ui_perf.
 */

import { getApi } from "./usePanelApi";
import { setVisibleInterval } from "../utils/visibleInterval";

export type UiPerfEntry = {
  kind: "ui_stall" | "ui_frame";
  name: string;
  duration_ms: number;
  pending_depth?: number;
  peak_pending?: number;
};

const FLUSH_INTERVAL_MS = 5000;
const LONGTASK_MS = 50;

let installed = false;
let buffer: UiPerfEntry[] = [];
let peakPending = 0;

function enqueue(entry: UiPerfEntry) {
  buffer.push(entry);
  if (buffer.length > 200) {
    buffer.splice(0, buffer.length - 200);
  }
}

function flush() {
  if (!buffer.length) return;
  const api = getApi();
  if (!api || typeof api.report_ui_perf !== "function") return;
  const batch = buffer.splice(0, buffer.length);
  try {
    void api.report_ui_perf(batch);
  } catch {
    // drop if bridge unavailable
  }
}

export function notePendingDepth(depth: number) {
  if (depth > peakPending) peakPending = depth;
  if (depth >= 50) {
    enqueue({
      kind: "ui_frame",
      name: "pending_events",
      duration_ms: 0,
      pending_depth: depth,
      peak_pending: peakPending,
    });
  }
}

export function noteFrameDelivery(durationMs: number, delivered: number, remaining: number) {
  if (durationMs >= 16 || remaining >= 50) {
    enqueue({
      kind: "ui_frame",
      name: "event_delivery",
      duration_ms: durationMs,
      pending_depth: remaining,
      peak_pending: peakPending,
    });
  }
  // silence unused when delivered is only for future diagnostics
  void delivered;
}

export function installPerfMonitor() {
  if (installed || typeof window === "undefined") return;
  installed = true;

  // The browser's own long-frame / long-task reports cost nothing while the page is idle.
  // A requestAnimationFrame sampler kept Chromium producing a frame on every vsync for as
  // long as the window was visible, which is all day next to UEFN.
  try {
    const Observer = (window as Window & { PerformanceObserver?: typeof PerformanceObserver }).PerformanceObserver;
    if (typeof Observer === "function") {
      const supported = Observer.supportedEntryTypes ?? [];
      const type = supported.includes("long-animation-frame") ? "long-animation-frame" : "longtask";
      const obs = new Observer((list) => {
        for (const entry of list.getEntries()) {
          if (entry.duration >= LONGTASK_MS) {
            enqueue({
              kind: "ui_stall",
              name: entry.name || "longtask",
              duration_ms: entry.duration,
              peak_pending: peakPending,
            });
          }
        }
      });
      try {
        obs.observe({ type, buffered: false });
      } catch {
        // entry type not supported here
      }
    }
  } catch {
    // ignore
  }

  // Don't wake to look at an empty buffer while the window is hidden.
  const stopFlush = setVisibleInterval(() => {
    if (buffer.length) flush();
  }, FLUSH_INTERVAL_MS);

  window.addEventListener("beforeunload", () => {
    flush();
    stopFlush();
  });
}

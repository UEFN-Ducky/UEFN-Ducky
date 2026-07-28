/**
 * Lightweight UI-thread stall monitor for the Ducky panel.
 * Reports longtasks and large rAF gaps to Python via PanelApi.report_ui_perf.
 */

import { getApi } from "./usePanelApi";

export type UiPerfEntry = {
  kind: "ui_stall" | "ui_frame";
  name: string;
  duration_ms: number;
  pending_depth?: number;
  peak_pending?: number;
};

const FLUSH_INTERVAL_MS = 5000;
const FRAME_GAP_MS = 250;
const LONGTASK_MS = 50;

let installed = false;
let buffer: UiPerfEntry[] = [];
let peakPending = 0;
let lastRaf = 0;
let flushTimer: ReturnType<typeof setInterval> | null = null;

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

  try {
    const Observer = (window as Window & { PerformanceObserver?: typeof PerformanceObserver }).PerformanceObserver;
    if (typeof Observer === "function") {
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
        obs.observe({ entryTypes: ["longtask"] });
      } catch {
        // longtask not supported in WebView2 — rely on rAF gaps
      }
    }
  } catch {
    // ignore
  }

  // WebView2/Chromium throttles rAF to ~1fps (or stops it) while the window is
  // hidden or fully occluded — those gaps are throttling, not UI jank. Skip the
  // measurement across any hidden period so raf_gap only reports real stalls.
  let skipNextGap = false;
  document.addEventListener("visibilitychange", () => {
    skipNextGap = true;
  });

  const tick = (now: number) => {
    if (lastRaf > 0 && !skipNextGap && !document.hidden) {
      const gap = now - lastRaf;
      if (gap >= FRAME_GAP_MS) {
        enqueue({
          kind: "ui_frame",
          name: "raf_gap",
          duration_ms: gap,
          peak_pending: peakPending,
        });
      }
    }
    skipNextGap = document.hidden;
    lastRaf = now;
    window.requestAnimationFrame(tick);
  };
  window.requestAnimationFrame(tick);

  flushTimer = window.setInterval(() => {
    if (buffer.length) flush();
  }, FLUSH_INTERVAL_MS);

  window.addEventListener("beforeunload", () => {
    flush();
    if (flushTimer != null) window.clearInterval(flushTimer);
  });
}

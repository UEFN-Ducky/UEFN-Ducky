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
  /** Number of observations represented by this interval's summary. */
  samples?: number;
  total_duration_ms?: number;
  delivered?: number;
  /** The long frame's slowest script: who called it and where it lives. */
  source?: string;
};

type LoafScript = { duration?: number; invoker?: string; sourceFunctionName?: string; sourceURL?: string };

/** "invoker fn @file:line" for a long animation frame's slowest script; undefined for a plain long task. */
export function stallSource(entry: PerformanceEntry): string | undefined {
  const scripts = (entry as PerformanceEntry & { scripts?: LoafScript[] }).scripts;
  if (!Array.isArray(scripts) || !scripts.length) return undefined;
  const top = scripts.reduce((a, b) => ((b.duration ?? 0) > (a.duration ?? 0) ? b : a));
  const file = (top.sourceURL || "").split(/[?#]/)[0].split("/").pop() || "";
  const text = [top.invoker, top.sourceFunctionName, file && `@${file}`].filter(Boolean).join(" ");
  return text ? text.slice(0, 160) : undefined;
}

const FLUSH_INTERVAL_MS = 5000;
const LONGTASK_MS = 50;

let installed = false;
let buffer: UiPerfEntry[] = [];
let peakPending = 0;
// Keep queue/delivery counters outside the stall buffer: a burst must not evict
// the long tasks that explain it. These summaries occupy constant space.
let pendingSummary: UiPerfEntry | undefined;
let deliverySummary: UiPerfEntry | undefined;

function enqueue(entry: UiPerfEntry) {
  buffer.push(entry);
  if (buffer.length > 200) {
    buffer.splice(0, buffer.length - 200);
  }
}

function flush() {
  if (!buffer.length && !pendingSummary && !deliverySummary) return;
  const api = getApi();
  if (!api || typeof api.report_ui_perf !== "function") return;
  const batch = buffer.splice(0, buffer.length);
  if (pendingSummary) batch.push(pendingSummary);
  if (deliverySummary) batch.push(deliverySummary);
  pendingSummary = undefined;
  deliverySummary = undefined;
  peakPending = 0;
  if (batch.length > 200) batch.splice(0, batch.length - 200);
  try {
    void api.report_ui_perf(batch);
  } catch {
    // drop if bridge unavailable
  }
}

export function notePendingDepth(depth: number) {
  if (depth > peakPending) peakPending = depth;
  if (depth >= 50) {
    if (!pendingSummary) {
      pendingSummary = { kind: "ui_frame", name: "pending_events", duration_ms: 0, samples: 0 };
    }
    pendingSummary.samples = (pendingSummary.samples ?? 0) + 1;
    pendingSummary.pending_depth = depth;
    pendingSummary.peak_pending = peakPending;
  }
}

export function noteFrameDelivery(durationMs: number, delivered: number, remaining: number) {
  if (remaining > peakPending) peakPending = remaining;
  if (durationMs >= 16 || remaining >= 50) {
    if (!deliverySummary) {
      deliverySummary = { kind: "ui_frame", name: "event_delivery", duration_ms: 0, samples: 0 };
    }
    deliverySummary.samples = (deliverySummary.samples ?? 0) + 1;
    deliverySummary.duration_ms = Math.max(deliverySummary.duration_ms, durationMs);
    deliverySummary.total_duration_ms = (deliverySummary.total_duration_ms ?? 0) + durationMs;
    deliverySummary.delivered = (deliverySummary.delivered ?? 0) + delivered;
    deliverySummary.pending_depth = remaining;
    deliverySummary.peak_pending = peakPending;
  }
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
            const source = stallSource(entry);
            enqueue({
              kind: "ui_stall",
              name: entry.name || "longtask",
              duration_ms: entry.duration,
              peak_pending: peakPending,
              ...(source ? { source } : {}),
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
    flush();
  }, FLUSH_INTERVAL_MS);

  window.addEventListener("beforeunload", () => {
    flush();
    stopFlush();
  });
}

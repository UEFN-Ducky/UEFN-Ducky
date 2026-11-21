/**
 * Live voice latency: time from the end of the user's turn to the first spoken audio.
 * Each sample goes to the dev console, the browser perf timeline and the app's perf log.
 */

import { getApi } from "../hooks/usePanelApi";

export type LiveTurnLane = "voice lane" | "agent" | "stop";

export type LiveLatencySample = {
  chatId: string;
  lane: LiveTurnLane;
  ms: number;
  label: string;
};

type Pending = { chatId: string; lane: LiveTurnLane; startedAt: number };

const MAX_SAMPLES = 20;
let pending: Pending | null = null;
const samples: LiveLatencySample[] = [];

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

/** The user's turn just ended (onTurnEnd): start the clock. */
export function markLiveTurnEnd(chatId: string, lane: LiveTurnLane = "agent", at = now()): void {
  pending = { chatId: (chatId || "").trim(), lane, startedAt: at };
  try {
    performance.mark?.("live-turn-end");
  } catch {
    /* timeline marks are optional */
  }
}

/** Routing decided who answers this turn. */
export function setLiveTurnLane(lane: LiveTurnLane): void {
  if (pending) pending.lane = lane;
}

/** Nothing will be spoken for this turn. */
export function cancelLiveTurnTimer(): void {
  pending = null;
}

export function formatLatency(ms: number, lane: LiveTurnLane): string {
  return `live turn ${(Math.max(0, ms) / 1000).toFixed(1)} s (${lane})`;
}

/** First TTS audio started: stop the clock and log the sample. */
export function markLiveFirstAudio(at = now()): LiveLatencySample | null {
  const run = pending;
  if (!run) return null;
  pending = null;
  const ms = Math.round(at - run.startedAt);
  const sample: LiveLatencySample = { chatId: run.chatId, lane: run.lane, ms, label: formatLatency(ms, run.lane) };
  samples.push(sample);
  if (samples.length > MAX_SAMPLES) samples.splice(0, samples.length - MAX_SAMPLES);
  console.info(`[live-voice] ${sample.label}`);
  try {
    performance.measure?.(sample.label, "live-turn-end");
  } catch {
    /* the start mark may be gone */
  }
  try {
    void getApi()?.report_ui_perf?.([
      { kind: "ui_frame", name: `live_turn_${run.lane.replace(" ", "_")}`, duration_ms: ms },
    ]);
  } catch {
    /* perf log is best effort */
  }
  return sample;
}

export function getLiveLatencySamples(): readonly LiveLatencySample[] {
  return samples;
}

/** Test helper. */
export function _resetLiveLatency(): void {
  pending = null;
  samples.length = 0;
}

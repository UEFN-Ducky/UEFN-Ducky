/**
 * FIFO speak queue for live voice: process chatter then the final answer.
 * One line at a time via ttsEngine.speak — no stomping, no silent drops (oldest
 * process lines are trimmed when the queue is full).
 */

import { ttsEngine } from "./ttsEngine";

export type LiveSpeakKind = "process" | "final";

type LiveSpeakItem = {
  text: string;
  voiceId: string;
  rate: number;
  kind: LiveSpeakKind;
};

/** Waiting process lines (not including the one currently playing). */
const MAX_PROCESS_WAITING = 2;

let items: LiveSpeakItem[] = [];
let pumping = false;
let gen = 0;

function trimProcessWaiting() {
  let waiting = 0;
  for (const item of items) {
    if (item.kind === "process") waiting += 1;
  }
  while (waiting > MAX_PROCESS_WAITING) {
    const idx = items.findIndex((i) => i.kind === "process");
    if (idx < 0) break;
    items.splice(idx, 1);
    waiting -= 1;
  }
}

async function waitIdle(myGen: number): Promise<void> {
  if (myGen !== gen) return;
  if (!ttsEngine.isSpeaking()) return;
  await new Promise<void>((resolve) => {
    const unsub = ttsEngine.onStateChange((s) => {
      if (s === "idle" || myGen !== gen) {
        unsub();
        resolve();
      }
    });
    if (!ttsEngine.isSpeaking() || myGen !== gen) {
      unsub();
      resolve();
    }
  });
}

async function pump() {
  if (pumping) return;
  pumping = true;
  const myGen = gen;
  try {
    while (items.length && myGen === gen) {
      const next = items.shift()!;
      ttsEngine.speak(next.text, next.voiceId, next.rate);
      await waitIdle(myGen);
    }
  } finally {
    pumping = false;
    if (items.length && myGen === gen) void pump();
  }
}

function push(item: LiveSpeakItem) {
  const cleaned = (item.text || "").trim();
  if (!cleaned) return;
  items.push({ ...item, text: cleaned });
  if (item.kind === "process") trimProcessWaiting();
  void pump();
}

/** Short status line (“Running tool…”, “Thinking…”). */
export function enqueueProcessSpeak(text: string, voiceId: string, rate: number) {
  push({ text, voiceId, rate, kind: "process" });
}

/** Final conversational answer — plays after any waiting process lines. */
export function enqueueFinalSpeak(text: string, voiceId: string, rate: number) {
  push({ text, voiceId, rate, kind: "final" });
}

/** Barge-in / skip / exit — wipe the queue and stop current speech. */
export function clearLiveSpeakQueue() {
  gen += 1;
  items = [];
  pumping = false;
  ttsEngine.cancel();
}

export function liveSpeakQueueLength(): number {
  return items.length + (ttsEngine.isSpeaking() ? 1 : 0);
}

/** Test helper. */
export function _liveSpeakQueueSnapshot(): { kinds: LiveSpeakKind[]; pumping: boolean } {
  return { kinds: items.map((i) => i.kind), pumping };
}

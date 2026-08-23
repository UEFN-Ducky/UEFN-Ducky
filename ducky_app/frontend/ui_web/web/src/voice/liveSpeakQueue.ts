/**
 * Shared live-voice timeline: one append-only list, one cursor, one speaker.
 * Nothing is dropped on append. Back / forward / newest move the cursor;
 * playback then continues forward from wherever it lands.
 */

import { getLiveVoiceChatIds } from "./liveChats";
import { ttsEngine } from "./ttsEngine";

export type LiveSpeakKind = "process" | "answer" | "final";

export type LiveSpeakOpts = {
  chatId: string;
  speaker?: string;
  voiceId: string;
  rate: number;
};

export type SpokenLine = {
  text: string | (() => Promise<string>);
  fallback?: string;
  resolvedText?: string;
  chatId: string;
  speaker: string;
  voiceId: string;
  rate: number;
  kind: LiveSpeakKind;
  skipped?: boolean;
};

export type LiveSpeakTransport = {
  hasPrev: boolean;
  hasNext: boolean;
  hasNewer: boolean;
  behind: number;
  cursor: number;
  length: number;
};

const MAX_LINES = 200;
const RESOLVE_MS = 4000;

let lines: SpokenLine[] = [];
let cursor = -1;
let pendingIndex: number | null = null;
let pumping = false;
let pumpGen = 0;
let parked = false;
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

export function subscribeLiveSpeakTransport(fn: () => void): () => void {
  listeners.add(fn);
  fn();
  return () => listeners.delete(fn);
}

function isPlayable(line: SpokenLine | undefined): boolean {
  return Boolean(line && !line.skipped);
}

function nextPlayableAfter(index: number): number | null {
  for (let i = index + 1; i < lines.length; i += 1) {
    if (isPlayable(lines[i])) return i;
  }
  return null;
}

function prevPlayableBefore(index: number): number | null {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (isPlayable(lines[i])) return i;
  }
  return null;
}

function lastPlayable(): number | null {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (isPlayable(lines[i])) return i;
  }
  return null;
}

function trimOldest() {
  while (lines.length > MAX_LINES) {
    lines.shift();
    cursor -= 1;
    if (pendingIndex != null) pendingIndex -= 1;
  }
  if (cursor < -1) cursor = -1;
  if (pendingIndex != null && pendingIndex < 0) pendingIndex = 0;
}

function countPlayableAfter(index: number): number {
  let n = 0;
  for (let i = index + 1; i < lines.length; i += 1) {
    if (isPlayable(lines[i])) n += 1;
  }
  return n;
}

export function getLiveSpeakTransport(): LiveSpeakTransport {
  const behind = countPlayableAfter(cursor);
  const canReplay = !ttsEngine.isSpeaking() && cursor >= 0 && isPlayable(lines[cursor]);
  return {
    hasPrev: canReplay || prevPlayableBefore(cursor) != null,
    hasNext: nextPlayableAfter(cursor) != null,
    hasNewer: behind > 0,
    behind,
    cursor,
    length: lines.length,
  };
}

export function getCurrentSpokenLine(): SpokenLine | null {
  if (cursor < 0 || cursor >= lines.length) return null;
  return lines[cursor];
}

function prefixIfNeeded(text: string, line: SpokenLine): string {
  if (getLiveVoiceChatIds().size <= 1) return text;
  const name = (line.speaker || "").trim();
  return name ? `${name}: ${text}` : text;
}

function timeoutFallback(fallback: string): Promise<string> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(fallback), RESOLVE_MS);
  });
}

async function resolveLine(line: SpokenLine): Promise<string> {
  if (typeof line.text === "string") {
    const cleaned = line.text.trim();
    line.resolvedText = cleaned;
    return cleaned;
  }
  if (line.resolvedText) return line.resolvedText;
  const fallback = (line.fallback || "").trim();
  try {
    const result = await Promise.race([line.text(), timeoutFallback(fallback)]);
    const cleaned = (result || fallback).trim();
    line.resolvedText = cleaned;
    line.text = cleaned;
    return cleaned;
  } catch {
    line.resolvedText = fallback;
    return fallback;
  }
}

async function waitIdle(): Promise<void> {
  if (!ttsEngine.isSpeaking()) return;
  await new Promise<void>((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      unsub();
      resolve();
    };
    const unsub = ttsEngine.onStateChange((s) => {
      if (s === "idle") finish();
    });
    const poll = () => {
      if (done) return;
      if (!ttsEngine.isSpeaking()) finish();
      else setTimeout(poll, 0);
    };
    poll();
  });
}

async function pump() {
  if (pumping) return;
  pumping = true;
  const myGen = ++pumpGen;
  try {
    while (myGen === pumpGen) {
      const idx = pendingIndex != null ? pendingIndex : parked ? null : nextPlayableAfter(cursor);
      pendingIndex = null;
      if (idx == null || idx < 0 || idx >= lines.length) break;
      const line = lines[idx];
      cursor = idx;
      notify();
      if (!isPlayable(line)) continue;
      const text = await resolveLine(line);
      if (myGen !== pumpGen) break;
      if (pendingIndex != null) continue;
      if (parked) break;
      if (!text) continue;
      ttsEngine.speak(prefixIfNeeded(text, line), line.voiceId, line.rate);
      await waitIdle();
      if (myGen !== pumpGen) break;
      if (pendingIndex != null) continue;
      if (parked) break;
    }
  } finally {
    if (myGen === pumpGen) pumping = false;
    if (myGen === pumpGen && (pendingIndex != null || (!parked && nextPlayableAfter(cursor) != null))) {
      void pump();
    }
  }
}

function seekTo(index: number) {
  parked = false;
  pendingIndex = index;
  ttsEngine.cancel();
  notify();
  void pump();
}

function push(kind: LiveSpeakKind, text: string | (() => Promise<string>), opts: LiveSpeakOpts, fallback?: string) {
  const chatId = (opts.chatId || "").trim();
  if (!chatId) return;
  if (typeof text === "string" && !text.trim()) return;
  lines.push({
    text: typeof text === "string" ? text.trim() : text,
    fallback: (fallback || "").trim() || undefined,
    chatId,
    speaker: (opts.speaker || "").trim(),
    voiceId: opts.voiceId,
    rate: opts.rate,
    kind,
  });
  trimOldest();
  parked = false;
  notify();
  if (!pumping && pendingIndex == null) pendingIndex = lines.length - 1;
  void pump();
}

export function enqueueProcessSpeak(
  text: string | (() => Promise<string>),
  opts: LiveSpeakOpts,
  fallback?: string,
) {
  push("process", text, opts, fallback);
}

export function enqueueAnswerSpeak(
  text: string | (() => Promise<string>),
  opts: LiveSpeakOpts,
  fallback?: string,
) {
  push("answer", text, opts, fallback);
}

export function enqueueFinalSpeak(
  text: string | (() => Promise<string>),
  opts: LiveSpeakOpts,
  fallback?: string,
) {
  push("final", text, opts, fallback);
}

/** Advance one playable line (existing Skip). */
export function speakNext() {
  const next = nextPlayableAfter(cursor);
  if (next == null) {
    parked = true;
    pendingIndex = null;
    ttsEngine.cancel();
    notify();
    return;
  }
  seekTo(next);
}

/** Previous playable line, or replay the current one when idle. */
export function speakPrev() {
  if (!ttsEngine.isSpeaking() && cursor >= 0 && isPlayable(lines[cursor])) {
    seekTo(cursor);
    return;
  }
  const prev = prevPlayableBefore(cursor);
  if (prev == null) return;
  seekTo(prev);
}

/** Jump to the newest playable line. History stays so Back still works. */
export function speakNewest() {
  const last = lastPlayable();
  if (last == null) return;
  if (last === cursor && ttsEngine.isSpeaking()) return;
  seekTo(last);
}

export function hasPrev(): boolean {
  return getLiveSpeakTransport().hasPrev;
}

export function hasNext(): boolean {
  return getLiveSpeakTransport().hasNext;
}

/** Stop talking, keep the list. New appends or transport resume. */
export function parkLiveSpeakQueue() {
  parked = true;
  pendingIndex = null;
  ttsEngine.cancel();
  notify();
}

/** Mark this chat's remaining lines skipped; cancel if it is currently speaking. */
export function clearLiveSpeakQueueForChat(chatId: string) {
  const id = (chatId || "").trim();
  if (!id) return;
  let cancelCurrent = false;
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].chatId !== id) continue;
    if (i >= cursor) {
      lines[i].skipped = true;
      if (i === cursor) cancelCurrent = true;
    }
  }
  if (cancelCurrent) {
    parked = false;
    pendingIndex = nextPlayableAfter(cursor);
    ttsEngine.cancel();
    void pump();
  }
  notify();
}

/** App teardown / tests — wipe everything. */
export function clearLiveSpeakQueue() {
  parked = true;
  pendingIndex = null;
  lines = [];
  cursor = -1;
  pumpGen += 1;
  pumping = false;
  ttsEngine.cancel();
  notify();
}

/** Remaining unplayed lines (+1 if TTS is mid-utterance). Parked = quiet for echo gates. */
export function liveSpeakQueueLength(): number {
  if (parked) return ttsEngine.isSpeaking() ? 1 : 0;
  return countPlayableAfter(cursor) + (ttsEngine.isSpeaking() ? 1 : 0);
}

/** Test helper. */
export function _liveSpeakQueueSnapshot(): {
  kinds: LiveSpeakKind[];
  pumping: boolean;
  cursor: number;
  skipped: boolean[];
  resolved: Array<string | null>;
} {
  return {
    kinds: lines.map((l) => l.kind),
    pumping,
    cursor,
    skipped: lines.map((l) => Boolean(l.skipped)),
    resolved: lines.map((l) => (typeof l.text === "string" ? l.text : l.resolvedText ?? null)),
  };
}

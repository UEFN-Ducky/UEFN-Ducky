import { stopLiveChat } from "./liveSpeakService";
/**
 * Live-voice presence + per-chat overlay state.
 * Spoken-summary skips chats that are currently live.
 */

import { onNativeLiveVoiceStop, syncNativeKeepAlive } from "./nativeKeepAlive";
import type { SpeechErrorAction } from "./speechErrors";

export type LiveVoiceUiStatus =
  | "off"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error"
  | "muted";

export type LiveVoiceState = {
  status: LiveVoiceUiStatus;
  userInterim: string;
  lastUserText: string;
  spokenText: string;
  error: string;
  /** One-click fix for the error (Windows speech settings, OpenAI key, …). */
  errorAction?: SpeechErrorAction;
  /** Non-fatal heads-up, e.g. which engine is listening. */
  notice: string;
  /** Mic off — type only; replies still speak. */
  muted: boolean;
  /** Current group-chat speaker name (empty for solo chats). */
  speakerName: string;
  /** Short hint like "2 more" when more duckies are queued to speak. */
  nextSpeaker: string;
};

const EMPTY: LiveVoiceState = {
  status: "off",
  userInterim: "",
  lastUserText: "",
  spokenText: "",
  error: "",
  errorAction: undefined,
  notice: "",
  muted: false,
  speakerName: "",
  nextSpeaker: "",
};

const live = new Set<string>();
const states = new Map<string, LiveVoiceState>();
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

function ensure(chatId: string): LiveVoiceState {
  let row = states.get(chatId);
  if (!row) {
    row = { ...EMPTY };
    states.set(chatId, row);
  }
  return row;
}

export function setLiveVoiceChat(chatId: string, on: boolean): void {
  const id = (chatId || "").trim();
  if (!id) return;
  if (on) {
    live.add(id);
    ensure(id);
  } else {
    live.delete(id);
    states.set(id, { ...EMPTY });
  }
  syncNativeKeepAlive(live.size);
  notify();
}

// Phone notification's Stop: end every live chat the same way End does.
onNativeLiveVoiceStop(() => {
  const ids = [...live];
  // After this handler returns: stopping inside it would re-enter the native Stop.
  void Promise.resolve().then(() => ids.forEach((id) => stopLiveChat(id)));
  return ids.length;
});

export function getLiveVoiceChatIds(): ReadonlySet<string> {
  return live;
}

export function getLiveVoiceState(chatId: string): LiveVoiceState {
  const id = (chatId || "").trim();
  if (!id) return { ...EMPTY };
  return { ...(states.get(id) || EMPTY) };
}

export function patchLiveVoiceState(chatId: string, patch: Partial<LiveVoiceState>): void {
  const id = (chatId || "").trim();
  if (!id) return;
  const prev = ensure(id);
  states.set(id, { ...prev, ...patch });
  notify();
}

export function subscribeLiveVoiceChats(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

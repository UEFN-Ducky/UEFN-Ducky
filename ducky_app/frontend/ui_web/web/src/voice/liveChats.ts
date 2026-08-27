/**
 * Live-voice presence + per-chat overlay state.
 * Spoken-summary skips chats that are currently live.
 */

export type LiveVoiceUiStatus = "off" | "listening" | "thinking" | "speaking" | "error" | "muted";

export type LiveVoiceState = {
  status: LiveVoiceUiStatus;
  userInterim: string;
  lastUserText: string;
  spokenText: string;
  error: string;
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
  notify();
}

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

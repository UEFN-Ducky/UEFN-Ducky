import { getApi } from "../hooks/usePanelApi";
import { clampProcessTalk } from "./processNarration";

export type SpokenStyle = "summary" | "speak_along";
/** Empty selects offline Windows dictation and system speech for live voice or remote devices. */
export type SttProvider = "" | "openai" | "webspeech";

export function normalizeSttProvider(raw: unknown): SttProvider {
  const s = String(raw || "").trim().toLowerCase();
  if (s === "openai" || s === "webspeech") return s;
  return "";
}

/** Sentence cap for live voice quick replies. */
export type ReplyLength = "short" | "normal" | "detailed";

export function normalizeReplyLength(raw: unknown): ReplyLength {
  const s = String(raw || "").trim().toLowerCase();
  return s === "normal" || s === "detailed" ? s : "short";
}

export const REPLY_LENGTH_OPTIONS: { value: ReplyLength; label: string }[] = [
  { value: "short", label: "Short: 1-2 sentences" },
  { value: "normal", label: "Normal: 3-4 sentences" },
  { value: "detailed", label: "Detailed: up to 6 sentences" },
];

export type VoiceSettings = {
  enabled: boolean;
  spokenStyle: SpokenStyle;
  summaryModel: string;
  defaultVoice: string;
  /** Global talking-speed multiplier (1 = normal). */
  defaultSpeed: number;
  /** Live voice: send each spoken turn on a pause. Off = words go into the chat box. */
  liveAutoSend: boolean;
  /**
   * How much live voice narrates tools/thinking while the agent works
   * (0 = mute process chatter, 1 = tools + thinking snippets). Final reply still speaks.
   */
  processTalk: number;
  /** Listen backend. Empty = system speech; openai needs an OpenAI key. */
  sttProvider: SttProvider;
  /** Live voice quick-reply model ("backend:model_id"). Empty = the chat's model. */
  liveModel: string;
  /** How long live voice's quick replies are. */
  replyLength: ReplyLength;
};

type VoiceLaneApi = {
  voice_lane_settings?: () => Promise<{ voice_live_model?: string; voice_reply_length?: string }>;
  save_voice_lane_settings?: (patch: Record<string, unknown>) => Promise<unknown>;
};

function laneApi(): VoiceLaneApi | null {
  return (getApi() as unknown as VoiceLaneApi | null) ?? null;
}

const DEFAULTS: VoiceSettings = {
  enabled: false,
  spokenStyle: "summary",
  summaryModel: "",
  defaultVoice: "",
  defaultSpeed: 1,
  liveAutoSend: false,
  processTalk: 0.7,
  sttProvider: "",
  liveModel: "",
  replyLength: "short",
};

/** Preset talking-speed choices for the ChoiceDropdown pickers (value is a number string). */
export const SPEED_OPTIONS: { value: string; label: string }[] = [
  { value: "0.5", label: "0.5× (slow)" },
  { value: "0.75", label: "0.75×" },
  { value: "0.9", label: "0.9×" },
  { value: "1", label: "1× (normal)" },
  { value: "1.1", label: "1.1×" },
  { value: "1.25", label: "1.25×" },
  { value: "1.5", label: "1.5×" },
  { value: "1.75", label: "1.75×" },
  { value: "2", label: "2×" },
  { value: "2.5", label: "2.5×" },
  { value: "3", label: "3×" },
  { value: "3.5", label: "3.5×" },
  { value: "4", label: "4× (max)" },
];

/** Clamp a speed multiplier to a safe playback range. */
export function clampSpeed(n: unknown): number {
  const v = typeof n === "number" ? n : Number(n);
  return Number.isFinite(v) && v > 0 ? Math.max(0.25, Math.min(4, v)) : 1;
}

/** Round to 2 decimals inside the clamp range (slider + radio shared). */
export function snapSpeed(n: unknown): number {
  return Math.round(clampSpeed(n) * 100) / 100;
}

/** Short trigger label, e.g. `1.5×`. */
export function formatSpeed(n: unknown): string {
  const v = snapSpeed(n);
  return `${Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}×`;
}

let cache: VoiceSettings = { ...DEFAULTS };
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

export function getVoiceSettings(): VoiceSettings {
  return cache;
}

export function subscribeVoiceSettings(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function loadVoiceSettings(): Promise<VoiceSettings> {
  const api = getApi();
  if (!api?.get_settings) return cache;
  const s = await api.get_settings();
  const lane = await Promise.resolve(laneApi()?.voice_lane_settings?.()).catch(() => undefined);
  cache = {
    enabled: Boolean(s.voice_enabled),
    spokenStyle: s.voice_spoken_style === "speak_along" ? "speak_along" : "summary",
    summaryModel: String(s.voice_summary_model || ""),
    defaultVoice: String(s.voice_default_voice || ""),
    defaultSpeed: clampSpeed(s.voice_default_speed),
    liveAutoSend: Boolean(s.voice_live_auto_send),
    processTalk: clampProcessTalk(s.voice_process_talk ?? 0.7),
    sttProvider: normalizeSttProvider(s.voice_stt_provider),
    liveModel: String(lane?.voice_live_model || "").trim(),
    replyLength: normalizeReplyLength(lane?.voice_reply_length),
  };
  notify();
  return cache;
}

export async function saveVoiceSettings(patch: Partial<VoiceSettings>): Promise<VoiceSettings> {
  const next: VoiceSettings = {
    enabled: patch.enabled ?? cache.enabled,
    spokenStyle: patch.spokenStyle ?? cache.spokenStyle,
    summaryModel: patch.summaryModel ?? cache.summaryModel,
    defaultVoice: patch.defaultVoice ?? cache.defaultVoice,
    defaultSpeed: patch.defaultSpeed != null ? clampSpeed(patch.defaultSpeed) : cache.defaultSpeed,
    liveAutoSend: patch.liveAutoSend ?? cache.liveAutoSend,
    processTalk: patch.processTalk != null ? clampProcessTalk(patch.processTalk) : cache.processTalk,
    sttProvider: patch.sttProvider != null ? normalizeSttProvider(patch.sttProvider) : cache.sttProvider,
    liveModel: patch.liveModel != null ? String(patch.liveModel).trim() : cache.liveModel,
    replyLength: patch.replyLength != null ? normalizeReplyLength(patch.replyLength) : cache.replyLength,
  };
  const laneChanged = next.liveModel !== cache.liveModel || next.replyLength !== cache.replyLength;
  cache = next;
  notify();
  if (laneChanged) {
    await laneApi()?.save_voice_lane_settings?.({
      voice_live_model: next.liveModel,
      voice_reply_length: next.replyLength,
    });
  }
  const api = getApi();
  if (api?.save_agent_settings) {
    await api.save_agent_settings({
      voice_enabled: next.enabled,
      voice_spoken_style: next.spokenStyle,
      voice_summary_model: next.summaryModel,
      voice_default_voice: next.defaultVoice,
      voice_default_speed: next.defaultSpeed,
      voice_live_auto_send: next.liveAutoSend,
      voice_process_talk: next.processTalk,
      voice_stt_provider: next.sttProvider,
    });
  }
  return next;
}

export function resolveVoiceId(duckyVoice?: string | null): string {
  const ducky = (duckyVoice || "").trim();
  if (ducky) return ducky;
  return cache.defaultVoice || "";
}

// Per-chat live voice model, kept on this device like the chat's voice and speed.
const CHAT_MODEL_KEY = "ducky.voice.chatLiveModels";
let chatModels: Record<string, string> | null = null;

function readChatModels(): Record<string, string> {
  if (chatModels) return chatModels;
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(CHAT_MODEL_KEY) : null;
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    chatModels = parsed && typeof parsed === "object" ? { ...(parsed as Record<string, string>) } : {};
  } catch {
    chatModels = {};
  }
  return chatModels;
}

export function getChatVoiceModel(chatId: string): string {
  const id = (chatId || "").trim();
  return id ? readChatModels()[id] || "" : "";
}

/** Set (or clear with "") this chat's voice model override. */
export function setChatVoiceModel(chatId: string, model: string): void {
  const id = (chatId || "").trim();
  if (!id) return;
  const map = readChatModels();
  const value = (model || "").trim();
  if (value) map[id] = value;
  else delete map[id];
  try {
    localStorage.setItem(CHAT_MODEL_KEY, JSON.stringify(map));
  } catch {
    /* storage full or blocked: the override still holds for this session */
  }
  notify();
}

/** This chat's voice model, else the global Live voice model, else "" (the chat's own model). */
export function resolveVoiceModel(chatId?: string | null): string {
  return getChatVoiceModel(chatId || "") || cache.liveModel || "";
}

/** Test helper. */
export function _resetVoiceSettingsCache(): void {
  cache = { ...DEFAULTS };
  chatModels = null;
}

/** Per-ducky speed (>0) wins; otherwise fall back to the global default speed. */
export function resolveSpeed(duckySpeed?: number | null): number {
  if (typeof duckySpeed === "number" && duckySpeed > 0) return clampSpeed(duckySpeed);
  return clampSpeed(cache.defaultSpeed);
}

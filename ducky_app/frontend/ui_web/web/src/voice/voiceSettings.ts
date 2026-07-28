import { getApi } from "../hooks/usePanelApi";
import { clampProcessTalk } from "./processNarration";

export type SpokenStyle = "summary" | "speak_along";

export type VoiceSettings = {
  enabled: boolean;
  spokenStyle: SpokenStyle;
  summaryModel: string;
  defaultVoice: string;
  /** Global talking-speed multiplier (1 = normal). */
  defaultSpeed: number;
  /** Live voice: accumulate until Send instead of auto-sending on pause. */
  liveManualSend: boolean;
  /**
   * How much live voice narrates tools/thinking while the agent works
   * (0 = mute process chatter, 1 = tools + thinking snippets). Final reply still speaks.
   */
  processTalk: number;
};

const DEFAULTS: VoiceSettings = {
  enabled: false,
  spokenStyle: "summary",
  summaryModel: "",
  defaultVoice: "",
  defaultSpeed: 1,
  liveManualSend: false,
  processTalk: 0.7,
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
  cache = {
    enabled: Boolean(s.voice_enabled),
    spokenStyle: s.voice_spoken_style === "speak_along" ? "speak_along" : "summary",
    summaryModel: String(s.voice_summary_model || ""),
    defaultVoice: String(s.voice_default_voice || ""),
    defaultSpeed: clampSpeed(s.voice_default_speed),
    liveManualSend: Boolean(s.voice_live_manual_send),
    processTalk: clampProcessTalk(s.voice_process_talk ?? 0.7),
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
    liveManualSend: patch.liveManualSend ?? cache.liveManualSend,
    processTalk: patch.processTalk != null ? clampProcessTalk(patch.processTalk) : cache.processTalk,
  };
  cache = next;
  notify();
  const api = getApi();
  if (api?.save_agent_settings) {
    await api.save_agent_settings({
      voice_enabled: next.enabled,
      voice_spoken_style: next.spokenStyle,
      voice_summary_model: next.summaryModel,
      voice_default_voice: next.defaultVoice,
      voice_default_speed: next.defaultSpeed,
      voice_live_manual_send: next.liveManualSend,
      voice_process_talk: next.processTalk,
    });
  }
  return next;
}

export function resolveVoiceId(duckyVoice?: string | null): string {
  const ducky = (duckyVoice || "").trim();
  if (ducky) return ducky;
  return cache.defaultVoice || "";
}

/** Per-ducky speed (>0) wins; otherwise fall back to the global default speed. */
export function resolveSpeed(duckySpeed?: number | null): number {
  if (typeof duckySpeed === "number" && duckySpeed > 0) return clampSpeed(duckySpeed);
  return clampSpeed(cache.defaultSpeed);
}

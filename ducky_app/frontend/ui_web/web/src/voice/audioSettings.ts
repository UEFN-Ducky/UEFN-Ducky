import { getApi } from "../hooks/usePanelApi";

export type MicPermission = "ask" | "allow" | "block";

export type AudioSettings = {
  micPermission: MicPermission;
  micDeviceId: string;
  outputDeviceId: string;
  ttsVolume: number;
  audioMuted: boolean;
};

const DEFAULTS: AudioSettings = {
  micPermission: "ask",
  micDeviceId: "",
  outputDeviceId: "",
  ttsVolume: 1,
  audioMuted: false,
};

let cache: AudioSettings = { ...DEFAULTS };
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 1;
  return Math.max(0, Math.min(1, n));
}

function normalizePermission(raw: unknown): MicPermission {
  const s = String(raw || "").trim();
  if (s === "allow" || s === "block" || s === "ask") return s;
  return "ask";
}

/** Effective output volume after master mute. */
export function effectivePlaybackVolume(volume: number, muted: boolean): number {
  return muted ? 0 : clamp01(volume);
}

export function getAudioSettings(): AudioSettings {
  return cache;
}

export function isAudioMuted(): boolean {
  return cache.audioMuted;
}

export function subscribeAudioSettings(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function loadAudioSettings(): Promise<AudioSettings> {
  const api = getApi();
  if (!api?.get_settings) return cache;
  const s = await api.get_settings();
  cache = {
    micPermission: normalizePermission(s.mic_permission),
    micDeviceId: String(s.mic_device_id || ""),
    outputDeviceId: String(s.output_device_id || ""),
    ttsVolume: clamp01(typeof s.tts_volume === "number" ? s.tts_volume : 1),
    audioMuted: Boolean(s.audio_muted),
  };
  notify();
  return cache;
}

export async function saveAudioSettings(patch: Partial<AudioSettings>): Promise<AudioSettings> {
  const next: AudioSettings = {
    micPermission: patch.micPermission ?? cache.micPermission,
    micDeviceId: patch.micDeviceId ?? cache.micDeviceId,
    outputDeviceId: patch.outputDeviceId ?? cache.outputDeviceId,
    ttsVolume: patch.ttsVolume != null ? clamp01(patch.ttsVolume) : cache.ttsVolume,
    audioMuted: patch.audioMuted ?? cache.audioMuted,
  };
  cache = next;
  notify();
  const api = getApi();
  if (api?.save_agent_settings) {
    await api.save_agent_settings({
      mic_permission: next.micPermission,
      mic_device_id: next.micDeviceId,
      output_device_id: next.outputDeviceId,
      tts_volume: next.ttsVolume,
      audio_muted: next.audioMuted,
    });
  }
  return next;
}

type SinkTarget = {
  setSinkId?: (sinkId: string) => Promise<void>;
};

/** Route HTMLMediaElement / AudioContext to the saved output device ("" = Windows default). */
export async function applyOutputDevice(target: SinkTarget): Promise<void> {
  const setSinkId = target.setSinkId;
  if (typeof setSinkId !== "function") return;
  const id = (cache.outputDeviceId || "").trim();
  try {
    await setSinkId.call(target, id);
  } catch {
    if (id) {
      try {
        await setSinkId.call(target, "");
      } catch {
        /* WebView may lack sink routing */
      }
    }
  }
}

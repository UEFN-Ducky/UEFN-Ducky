/**
 * Resolve hook → soundRef and play. Independent of emit sites.
 *
 * soundRef forms: builtin:<name> | plugin:<pluginId>:<soundId> | file:<filename> | "" (none)
 */

import { PLUGIN_UI_ROUTE_PREFIX } from "../plugin-ui/constants";
import { applyOutputDevice, effectivePlaybackVolume, isAudioMuted } from "../voice/audioSettings";
import { isBuiltinSoundName, playBuiltinSound, type BuiltinSoundName } from "./builtinSounds";

export type SoundRef = string;

export interface SoundsSettings {
  enabled: boolean;
  volume: number;
  /** hookId → soundRef */
  mapping: Record<string, SoundRef>;
}

export const DEFAULT_SOUNDS: SoundsSettings = {
  enabled: false,
  volume: 0.5,
  mapping: {
    "tab.changed": "builtin:click",
    "settings.opened": "builtin:pop",
    "agent.selected": "builtin:click",
    "agent.done": "builtin:ding",
    "agent.error": "builtin:error-buzz",
    "verse.errors": "builtin:error-buzz",
  },
};

export function normalizeSounds(raw: unknown): SoundsSettings {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_SOUNDS, mapping: { ...DEFAULT_SOUNDS.mapping } };
  const o = raw as Record<string, unknown>;
  const mapping: Record<string, SoundRef> = { ...DEFAULT_SOUNDS.mapping };
  if (o.mapping && typeof o.mapping === "object") {
    for (const [k, v] of Object.entries(o.mapping as Record<string, unknown>)) {
      if (typeof k === "string" && typeof v === "string") mapping[k] = v;
    }
  }
  const volume =
    typeof o.volume === "number" && Number.isFinite(o.volume)
      ? Math.max(0, Math.min(1, o.volume))
      : DEFAULT_SOUNDS.volume;
  return {
    enabled: typeof o.enabled === "boolean" ? o.enabled : DEFAULT_SOUNDS.enabled,
    volume,
    mapping,
  };
}

/** Resolve soundRef for a hook; empty string = silence. */
export function resolveSoundRef(settings: SoundsSettings, hookId: string): SoundRef {
  const ref = settings.mapping[hookId];
  return typeof ref === "string" ? ref : "";
}

export function parseSoundRef(
  ref: SoundRef,
):
  | { kind: "none" }
  | { kind: "builtin"; name: BuiltinSoundName }
  | { kind: "plugin"; pluginId: string; soundId: string }
  | { kind: "file"; filename: string } {
  const s = (ref || "").trim();
  if (!s) return { kind: "none" };
  if (s.startsWith("builtin:")) {
    const name = s.slice("builtin:".length);
    if (isBuiltinSoundName(name)) return { kind: "builtin", name };
    return { kind: "none" };
  }
  if (s.startsWith("plugin:")) {
    const rest = s.slice("plugin:".length);
    const i = rest.indexOf(":");
    if (i <= 0) return { kind: "none" };
    const pluginId = rest.slice(0, i);
    const soundId = rest.slice(i + 1);
    if (!pluginId || !soundId) return { kind: "none" };
    return { kind: "plugin", pluginId, soundId };
  }
  if (s.startsWith("file:")) {
    const filename = s.slice("file:".length).replace(/[/\\]/g, "");
    if (!filename) return { kind: "none" };
    return { kind: "file", filename };
  }
  return { kind: "none" };
}

export function soundUrlForRef(
  ref: SoundRef,
  pluginFileByKey?: Record<string, string>,
): string | null {
  const parsed = parseSoundRef(ref);
  if (parsed.kind === "plugin") {
    const key = `${parsed.pluginId}:${parsed.soundId}`;
    const file = pluginFileByKey?.[key];
    if (!file) return null;
    return `/${PLUGIN_UI_ROUTE_PREFIX}/${encodeURIComponent(parsed.pluginId)}/${file
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`;
  }
  if (parsed.kind === "file") {
    return `/user-sounds/${encodeURIComponent(parsed.filename)}`;
  }
  return null;
}

const audioCache = new Map<string, HTMLAudioElement>();

function playUrl(url: string, volume: number): void {
  let el = audioCache.get(url);
  if (!el) {
    el = new Audio(url);
    audioCache.set(url, el);
  }
  el.volume = Math.max(0, Math.min(1, volume));
  el.currentTime = 0;
  void applyOutputDevice(el).then(() => el!.play()).catch(() => {
    /* autoplay / missing file / sink — ignore */
  });
}

export function playSoundRef(
  ref: SoundRef,
  volume: number,
  pluginFileByKey?: Record<string, string>,
  opts?: { ignoreMasterMute?: boolean },
): void {
  const parsed = parseSoundRef(ref);
  if (parsed.kind === "none") return;
  const vol =
    opts?.ignoreMasterMute
      ? Math.max(0, Math.min(1, volume))
      : effectivePlaybackVolume(volume, isAudioMuted());
  if (vol <= 0 && !opts?.ignoreMasterMute) return;
  if (parsed.kind === "builtin") {
    playBuiltinSound(parsed.name, vol);
    return;
  }
  const url = soundUrlForRef(ref, pluginFileByKey);
  if (url) playUrl(url, vol);
}

export function playHookSound(
  settings: SoundsSettings,
  hookId: string,
  pluginFileByKey?: Record<string, string>,
): void {
  if (!settings.enabled || isAudioMuted()) return;
  const ref = resolveSoundRef(settings, hookId);
  if (!ref) return;
  playSoundRef(ref, settings.volume, pluginFileByKey);
}

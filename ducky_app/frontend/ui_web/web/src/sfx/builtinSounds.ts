/**
 * Built-in WebAudio synth presets — no asset files.
 */

import { applyOutputDevice } from "../voice/audioSettings";

export type BuiltinSoundName = "click" | "pop" | "chime" | "ding" | "error-buzz";

export const BUILTIN_SOUNDS: { id: BuiltinSoundName; label: string }[] = [
  { id: "click", label: "Click" },
  { id: "pop", label: "Pop" },
  { id: "chime", label: "Chime" },
  { id: "ding", label: "Ding" },
  { id: "error-buzz", label: "Error buzz" },
];

let sharedCtx: AudioContext | null = null;
let sinkReady: Promise<void> = Promise.resolve();

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AC =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  if (!sharedCtx) sharedCtx = new AC();
  if (sharedCtx.state === "suspended") void sharedCtx.resume();
  return sharedCtx;
}

function ensureSink(ctx: AudioContext): Promise<void> {
  sinkReady = applyOutputDevice(
    ctx as unknown as { setSinkId?: (id: string) => Promise<void> },
  );
  return sinkReady;
}

/** Re-apply saved output device to the shared AudioContext (after settings change). */
export function syncBuiltinOutputDevice(): void {
  if (!sharedCtx) return;
  void ensureSink(sharedCtx);
}

function tone(
  ctx: AudioContext,
  freq: number,
  start: number,
  dur: number,
  type: OscillatorType,
  gainPeak: number,
  volume: number,
): void {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  const peak = Math.max(0, Math.min(1, volume)) * gainPeak;
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(start);
  osc.stop(start + dur + 0.02);
}

export function playBuiltinSound(name: BuiltinSoundName, volume = 0.5): void {
  const ctx = getCtx();
  if (!ctx) return;
  const v = Math.max(0, Math.min(1, volume));
  void ensureSink(ctx).then(() => {
    const t = ctx.currentTime;
    switch (name) {
      case "click":
        tone(ctx, 1800, t, 0.04, "square", 0.15, v);
        break;
      case "pop":
        tone(ctx, 420, t, 0.08, "sine", 0.25, v);
        tone(ctx, 280, t + 0.02, 0.06, "sine", 0.12, v);
        break;
      case "chime":
        tone(ctx, 880, t, 0.25, "sine", 0.2, v);
        tone(ctx, 1320, t + 0.05, 0.3, "sine", 0.12, v);
        break;
      case "ding":
        tone(ctx, 1046, t, 0.35, "triangle", 0.22, v);
        tone(ctx, 1568, t + 0.08, 0.4, "triangle", 0.1, v);
        break;
      case "error-buzz":
        tone(ctx, 140, t, 0.18, "sawtooth", 0.18, v);
        tone(ctx, 110, t + 0.1, 0.22, "sawtooth", 0.14, v);
        break;
      default:
        break;
    }
  });
}

export function isBuiltinSoundName(value: string): value is BuiltinSoundName {
  return BUILTIN_SOUNDS.some((s) => s.id === value);
}

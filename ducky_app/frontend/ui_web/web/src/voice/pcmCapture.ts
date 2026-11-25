export const TARGET_RATE = 24000;
const MIN_SECONDS = 0.15;
const MIN_PEAK = 0.008;
export type PcmCapture = {
  sampleRate: number;
  stop: () => Promise<void>;
};

/** Linear resample Float32 PCM to a target sample rate. */
export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (!input.length || fromRate <= 0 || toRate <= 0 || fromRate === toRate) {
    return input;
  }
  const ratio = fromRate / toRate;
  const outLen = Math.max(1, Math.round(input.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i += 1) {
    const src = i * ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const t = src - i0;
    out[i] = input[i0]! * (1 - t) + input[i1]! * t;
  }
  return out;
}

export function isTooShortRecording(samples: Float32Array, sampleRate: number): boolean {
  const rate = sampleRate > 0 ? sampleRate : TARGET_RATE;
  if (samples.length < rate * MIN_SECONDS) return true;
  let peak = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const a = Math.abs(samples[i]!);
    if (a > peak) peak = a;
  }
  return peak < MIN_PEAK;
}

export function encodeWavPcm16(samples: Float32Array, sampleRate: number): Blob {
  const pcm = floatTo16BitPcm(samples);
  const dataBytes = pcm.byteLength;
  const buf = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buf);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);
  new Uint8Array(buf, 44).set(new Uint8Array(pcm.buffer, pcm.byteOffset, dataBytes));
  return new Blob([buf], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
}

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(blob);
  });
}

export function floatTo16BitPcm(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const s = Math.max(-1, Math.min(1, input[i]!));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function int16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function concatFloat32(parts: Float32Array[]): Float32Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Float32Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

export async function startPcmCapture(
  stream: MediaStream,
  onFrame: (input: Float32Array, sampleRate: number) => void,
): Promise<PcmCapture> {
  let audioCtx: AudioContext;
  try {
    audioCtx = new AudioContext();
    if (audioCtx.state === "suspended") await audioCtx.resume();
  } catch (err) {
    throw new Error(
      err instanceof Error
        ? err.message
        : "The audio device is not available. Pick another output in Settings → Audio.",
    );
  }
  const sampleRate = audioCtx.sampleRate || 48000;
  const source = audioCtx.createMediaStreamSource(stream);
  // ponytail: ScriptProcessor is deprecated but works in WebView2 without an AudioWorklet file URL.
  const processor = audioCtx.createScriptProcessor(4096, 1, 1);
  const mute = audioCtx.createGain();
  mute.gain.value = 0;
  processor.onaudioprocess = (e) => {
    onFrame(e.inputBuffer.getChannelData(0), sampleRate);
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(audioCtx.destination);
  return {
    sampleRate,
    stop: async () => {
      try {
        processor.disconnect();
      } catch {
        /* ignore */
      }
      try {
        source.disconnect();
      } catch {
        /* ignore */
      }
      try {
        mute.disconnect();
      } catch {
        /* ignore */
      }
      stream.getTracks().forEach((t) => t.stop());
      await audioCtx.close().catch(() => undefined);
    },
  };
}

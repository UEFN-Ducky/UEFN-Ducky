/**
 * TranscriptionSession — one interface, two backends:
 *   default: browser SpeechRecognition (no API key)
 *   openai:  batch Whisper REST / Realtime WS when an OpenAI key is saved
 *
 * OpenAI PCM capture uses a default-rate AudioContext like Settings → Input.
 * Forcing sampleRate: 24000 made MediaStreamSource silent in WebView2.
 */

import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { getApi } from "../hooks/usePanelApi";
import { requestMicAccess } from "./micPermission";
import { getVoiceSettings, normalizeSttProvider } from "./voiceSettings";

export type TranscriptionHandlers = {
  onInterim?: (text: string) => void;
  onFinal?: (text: string) => void;
  onSpeechStarted?: () => void;
  onSpeechStopped?: () => void;
  onError?: (message: string) => void;
  onStateChange?: (state: TranscriptionState) => void;
};

export type TranscriptionState = "idle" | "listening" | "transcribing" | "error";

export interface TranscriptionSession {
  readonly kind: "batch" | "streaming";
  start(handlers?: TranscriptionHandlers): Promise<void>;
  stop(): Promise<void>;
  abort(): void;
}

export type SttBackend = "webspeech" | "openai";

type SpeechRecCtor = new () => BrowserSpeechRec;

type BrowserSpeechRec = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((ev: BrowserSpeechResultEvent) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onend: (() => void) | null;
  onspeechstart: (() => void) | null;
  onspeechend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type BrowserSpeechResultEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal?: boolean; 0?: { transcript?: string } }>;
};

export function getSpeechRecognitionCtor(): SpeechRecCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as Window & {
    SpeechRecognition?: SpeechRecCtor;
    webkitSpeechRecognition?: SpeechRecCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function pickTranscriptionBackend(opts: {
  preference?: string;
  speechAvailable: boolean;
  openaiReady: boolean;
}): SttBackend {
  const pref = normalizeSttProvider(opts.preference);
  if (pref === "openai") {
    if (opts.openaiReady) return "openai";
    if (opts.speechAvailable) return "webspeech";
    return "openai";
  }
  if (pref === "webspeech") {
    if (opts.speechAvailable) return "webspeech";
    if (opts.openaiReady) return "openai";
    return "webspeech";
  }
  if (opts.speechAvailable) return "webspeech";
  return "openai";
}

async function openaiVoiceReady(): Promise<boolean> {
  try {
    const status = await getApi()?.get_key_status?.();
    return Boolean(status && (status as { openai?: boolean }).openai);
  } catch {
    return false;
  }
}

async function resolveSttBackend(): Promise<SttBackend> {
  return pickTranscriptionBackend({
    preference: getVoiceSettings().sttProvider,
    speechAvailable: Boolean(getSpeechRecognitionCtor()),
    openaiReady: await openaiVoiceReady(),
  });
}

function wrapSession(kind: "batch" | "streaming", pick: () => Promise<TranscriptionSession>): TranscriptionSession {
  let inner: TranscriptionSession | null = null;
  return {
    kind,
    async start(handlers) {
      inner?.abort();
      inner = await pick();
      await inner.start(handlers);
    },
    async stop() {
      await inner?.stop();
    },
    abort() {
      inner?.abort();
      inner = null;
    },
  };
}

const TARGET_RATE = 24000;
const MIN_SECONDS = 0.15;
const MIN_PEAK = 0.008;

type PcmCapture = {
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

function blobToBase64(blob: Blob): Promise<string> {
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

function floatTo16BitPcm(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const s = Math.max(-1, Math.min(1, input[i]!));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function int16ToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function concatFloat32(parts: Float32Array[]): Float32Array {
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

async function startPcmCapture(
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

/** Push-to-talk: record until stop(), then Whisper REST. */
function createOpenAiBatchTranscriptionSession(): TranscriptionSession {
  let capture: PcmCapture | null = null;
  let chunks: Float32Array[] = [];
  let handlers: TranscriptionHandlers = {};
  let state: TranscriptionState = "idle";

  const setState = (next: TranscriptionState) => {
    state = next;
    handlers.onStateChange?.(next);
  };

  const dropCapture = () => {
    const running = capture;
    capture = null;
    chunks = [];
    if (running) void running.stop();
  };

  return {
    kind: "batch",
    async start(h = {}) {
      handlers = h;
      if (state === "listening" || state === "transcribing") return;
      chunks = [];
      const media = await requestMicAccess();
      capture = await startPcmCapture(media, (input) => {
        chunks.push(new Float32Array(input));
      });
      setState("listening");
    },
    async stop() {
      if (!capture || state !== "listening") {
        this.abort();
        return;
      }
      const running = capture;
      const parts = chunks;
      capture = null;
      chunks = [];
      setState("transcribing");
      try {
        const sampleRate = running.sampleRate;
        await running.stop();
        const samples = concatFloat32(parts);
        if (isTooShortRecording(samples, sampleRate)) {
          handlers.onError?.("Recording too short");
          setState("error");
          return;
        }
        const blob = encodeWavPcm16(samples, sampleRate);
        const b64 = await blobToBase64(blob);
        const result = await runBridgeJob<{ ok?: boolean; text?: string; error?: string }>(
          "voice_transcribe_audio",
          [b64, "audio/wav"],
          90_000,
        );
        if (!result?.ok) {
          handlers.onError?.(String(result?.error || "Transcription failed"));
          setState("error");
          return;
        }
        const text = String(result.text || "").trim();
        if (text) handlers.onFinal?.(text);
        setState("idle");
      } catch (err) {
        handlers.onError?.(err instanceof Error ? err.message : String(err));
        setState("error");
      }
    },
    abort() {
      dropCapture();
      setState("idle");
    },
  };
}

type TokenResult = {
  ok?: boolean;
  value?: string;
  ws_url?: string;
  error?: string;
};

/** Live streaming STT via OpenAI Realtime transcription WebSocket (GA). */
function createOpenAiStreamingTranscriptionSession(): TranscriptionSession {
  let capture: PcmCapture | null = null;
  let ws: WebSocket | null = null;
  let handlers: TranscriptionHandlers = {};
  let state: TranscriptionState = "idle";
  let interim = "";
  let closed = false;

  const setState = (next: TranscriptionState) => {
    state = next;
    handlers.onStateChange?.(next);
  };

  const cleanupAudio = () => {
    const running = capture;
    capture = null;
    if (running) void running.stop();
  };

  const closeWs = () => {
    if (ws && ws.readyState <= WebSocket.OPEN) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    ws = null;
  };

  return {
    kind: "streaming",
    async start(h = {}) {
      handlers = h;
      closed = false;
      if (state === "listening") return;

      let token: TokenResult;
      try {
        const api = getApi();
        if (api?.bridge_job_start) {
          token = await runBridgeJob<TokenResult>("voice_create_realtime_token", [], 30_000);
        } else {
          token = (await (api as { voice_create_realtime_token?: () => Promise<TokenResult> } | null)
            ?.voice_create_realtime_token?.()) || { ok: false, error: "voice API unavailable" };
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        throw new Error(`Could not create realtime token: ${msg}`);
      }
      if (!token?.ok || !token.value) {
        throw new Error(String(token?.error || "Could not create realtime token"));
      }

      const media = await requestMicAccess();
      const wsUrl = token.ws_url || "wss://api.openai.com/v1/realtime?intent=transcription";
      // GA handshake only — the beta subprotocol routes to the retired Beta API.
      try {
        ws = new WebSocket(wsUrl, ["realtime", `openai-insecure-api-key.${token.value}`]);

        await new Promise<void>((resolve, reject) => {
          if (!ws) return reject(new Error("no websocket"));
          const timer = window.setTimeout(() => reject(new Error("Realtime WS timeout")), 15_000);
          ws.onopen = () => {
            window.clearTimeout(timer);
            resolve();
          };
          ws.onerror = () => {
            window.clearTimeout(timer);
            reject(new Error("Realtime WS failed to connect"));
          };
          ws.onclose = (ev) => {
            window.clearTimeout(timer);
            if (!closed) {
              const detail = [ev.code, ev.reason].filter(Boolean).join(" ");
              reject(new Error(detail ? `Realtime WS closed: ${detail}` : "Realtime WS closed before open"));
            }
          };
        });
      } catch (err) {
        media.getTracks().forEach((t) => t.stop());
        closeWs();
        throw err;
      }

      ws.onmessage = (ev) => {
        if (closed) return;
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(String(ev.data || "{}")) as Record<string, unknown>;
        } catch {
          return;
        }
        const type = String(event.type || "");
        if (type === "input_audio_buffer.speech_started") {
          handlers.onSpeechStarted?.();
          return;
        }
        if (type === "input_audio_buffer.speech_stopped") {
          handlers.onSpeechStopped?.();
          return;
        }
        if (
          type === "conversation.item.input_audio_transcription.delta" ||
          type === "transcription_session.delta" ||
          type.endsWith("transcription.delta")
        ) {
          const delta = String(event.delta || "");
          if (delta) {
            interim += delta;
            handlers.onInterim?.(interim);
          }
          return;
        }
        if (
          type === "conversation.item.input_audio_transcription.completed" ||
          type.endsWith("transcription.completed")
        ) {
          const finalText = String(event.transcript || interim || "").trim();
          interim = "";
          if (finalText) handlers.onFinal?.(finalText);
          handlers.onInterim?.("");
          return;
        }
        if (type === "error") {
          const err =
            typeof event.error === "object" && event.error
              ? String((event.error as { message?: string }).message || "Realtime error")
              : "Realtime error";
          handlers.onError?.(err);
          setState("error");
        }
      };

      ws.onclose = (ev) => {
        if (closed) return;
        cleanupAudio();
        if (state === "listening" || state === "idle") {
          const detail = [ev.code, ev.reason].filter(Boolean).join(" ");
          if (detail && ev.code !== 1000) {
            handlers.onError?.(`Realtime connection closed: ${detail}`);
            setState("error");
            return;
          }
          setState("idle");
        }
      };

      // Session config is bound to the ephemeral client secret — no session.update.

      try {
        capture = await startPcmCapture(media, (input, actualRate) => {
          if (!ws || ws.readyState !== WebSocket.OPEN || closed) return;
          const resampled = resampleLinear(input, actualRate, TARGET_RATE);
          const pcm = floatTo16BitPcm(resampled);
          ws.send(
            JSON.stringify({
              type: "input_audio_buffer.append",
              audio: int16ToBase64(pcm),
            }),
          );
        });
      } catch (err) {
        closeWs();
        media.getTracks().forEach((t) => t.stop());
        throw err;
      }
      setState("listening");
    },
    async stop() {
      closed = true;
      closeWs();
      cleanupAudio();
      interim = "";
      setState("idle");
    },
    abort() {
      closed = true;
      closeWs();
      cleanupAudio();
      interim = "";
      setState("idle");
    },
  };
}

function createWebSpeechTranscriptionSession(kind: "batch" | "streaming"): TranscriptionSession {
  const Ctor = getSpeechRecognitionCtor();
  let rec: BrowserSpeechRec | null = null;
  let handlers: TranscriptionHandlers = {};
  let state: TranscriptionState = "idle";
  let closed = false;
  let restartOnEnd = kind === "streaming";

  const setState = (next: TranscriptionState) => {
    state = next;
    handlers.onStateChange?.(next);
  };

  const drop = () => {
    const running = rec;
    rec = null;
    if (!running) return;
    running.onresult = null;
    running.onerror = null;
    running.onend = null;
    running.onspeechstart = null;
    running.onspeechend = null;
    try {
      running.abort();
    } catch {
      /* ignore */
    }
  };

  const bind = (speech: BrowserSpeechRec) => {
    speech.continuous = true;
    speech.interimResults = true;
    speech.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
    speech.onspeechstart = () => handlers.onSpeechStarted?.();
    speech.onspeechend = () => handlers.onSpeechStopped?.();
    speech.onresult = (ev) => {
      let interim = "";
      let finals = "";
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
        const row = ev.results[i];
        const piece = String(row?.[0]?.transcript || "").trim();
        if (!piece) continue;
        if (row?.isFinal) finals = finals ? `${finals} ${piece}` : piece;
        else interim = interim ? `${interim} ${piece}` : piece;
      }
      if (interim) handlers.onInterim?.(interim);
      if (finals) {
        handlers.onInterim?.("");
        handlers.onFinal?.(finals);
      }
    };
    speech.onerror = (ev) => {
      const code = String(ev.error || "");
      if (code === "aborted" || code === "no-speech") return;
      handlers.onError?.(code === "not-allowed" ? "Microphone blocked" : `Speech error: ${code || "failed"}`);
      setState("error");
    };
    speech.onend = () => {
      if (closed || !restartOnEnd || state !== "listening") {
        if (!closed && state === "listening") setState("idle");
        return;
      }
      try {
        speech.start();
      } catch {
        /* Chrome throws if start races abort */
      }
    };
  };

  return {
    kind,
    async start(h = {}) {
      handlers = h;
      closed = false;
      restartOnEnd = kind === "streaming";
      if (!Ctor) throw new Error("Browser speech is not available on this device.");
      // Prime the in-app mic prompt, then release so SpeechRecognition can own the device.
      const media = await requestMicAccess();
      media.getTracks().forEach((t) => t.stop());
      drop();
      const speech = new Ctor();
      rec = speech;
      bind(speech);
      speech.start();
      setState("listening");
    },
    async stop() {
      restartOnEnd = false;
      closed = true;
      const running = rec;
      rec = null;
      if (running) {
        running.onend = () => setState("idle");
        try {
          running.stop();
        } catch {
          setState("idle");
        }
      } else {
        setState("idle");
      }
    },
    abort() {
      restartOnEnd = false;
      closed = true;
      drop();
      setState("idle");
    },
  };
}

/** Push-to-talk dictation — system speech first, OpenAI Whisper if that's what you picked. */
export function createBatchTranscriptionSession(): TranscriptionSession {
  return wrapSession("batch", async () =>
    (await resolveSttBackend()) === "webspeech"
      ? createWebSpeechTranscriptionSession("batch")
      : createOpenAiBatchTranscriptionSession(),
  );
}

/** Live streaming STT — system speech first, OpenAI Realtime when a key is saved. */
export function createStreamingTranscriptionSession(): TranscriptionSession {
  return wrapSession("streaming", async () =>
    (await resolveSttBackend()) === "webspeech"
      ? createWebSpeechTranscriptionSession("streaming")
      : createOpenAiStreamingTranscriptionSession(),
  );
}

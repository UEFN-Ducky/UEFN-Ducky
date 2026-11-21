/**
 * TranscriptionSession — one interface, three engines:
 *   windows:   Windows dictation through the desktop backend (no key, live words)
 *   webspeech: browser SpeechRecognition — phones / real browsers only
 *   openai:    OpenAI Realtime (live words) when an OpenAI key is saved
 *
 * The desktop app is WebView2: it exposes SpeechRecognition but has no speech
 * service behind it, so start() "works" and then fails with `network`. The app
 * therefore never picks browser speech — it uses Windows speech or OpenAI.
 *
 * OpenAI PCM capture uses a default-rate AudioContext like Settings → Input.
 * Forcing sampleRate: 24000 made MediaStreamSource silent in WebView2.
 */

import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { getApi, isRemote } from "../hooks/usePanelApi";
import type { WinSttEvent } from "../types/panel";
import { requestMicAccess } from "./micPermission";
import { actionForCode, SpeechError, toSpeechError } from "./speechErrors";
import { getVoiceSettings, normalizeSttProvider } from "./voiceSettings";

export type TranscriptionHandlers = {
  onInterim?: (text: string) => void;
  onFinal?: (text: string) => void;
  onSpeechStarted?: () => void;
  onSpeechStopped?: () => void;
  onError?: (error: SpeechError) => void;
  onStateChange?: (state: TranscriptionState) => void;
  /** Non-fatal heads-up, e.g. "No OpenAI key — using Windows speech". */
  onNotice?: (message: string) => void;
};

export type TranscriptionState = "idle" | "connecting" | "listening" | "transcribing" | "error";

export interface TranscriptionSession {
  readonly kind: "batch" | "streaming";
  start(handlers?: TranscriptionHandlers): Promise<void>;
  /** Stop listening. Resolves after the last spoken words arrived as finals. */
  stop(): Promise<void>;
  abort(): void;
}

export type SttBackend = "windows" | "webspeech" | "openai" | "none";

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

/** The desktop app (pywebview / WebView2) — browser speech is a dead stub here. */
export function isDesktopWebView(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as Window & { pywebview?: unknown; chrome?: { webview?: unknown } };
  return Boolean(w.pywebview || w.chrome?.webview);
}

/** Browser speech that actually has a service behind it (phones, real Chrome/Edge). */
export function browserSpeechUsable(): boolean {
  return Boolean(getSpeechRecognitionCtor()) && !isDesktopWebView();
}

/** Windows dictation via the backend — desktop only (a phone must not open the PC mic). */
export function windowsSpeechUsable(): boolean {
  return !isRemote() && typeof getApi()?.voice_win_stt_start === "function";
}

export type BackendChoice = {
  backend: SttBackend;
  /** OpenAI was picked but no key is saved — the system engine stands in. */
  openaiMissingKey: boolean;
};

export function pickTranscriptionBackend(opts: {
  preference?: string;
  windowsSpeech: boolean;
  browserSpeech: boolean;
  openaiReady: boolean;
}): BackendChoice {
  const pref = normalizeSttProvider(opts.preference);
  const system: SttBackend = opts.windowsSpeech ? "windows" : opts.browserSpeech ? "webspeech" : "none";
  if (pref === "openai") {
    if (opts.openaiReady) return { backend: "openai", openaiMissingKey: false };
    return { backend: system, openaiMissingKey: true };
  }
  if (system !== "none") return { backend: system, openaiMissingKey: false };
  if (opts.openaiReady) return { backend: "openai", openaiMissingKey: false };
  return { backend: "none", openaiMissingKey: false };
}

let keyCache: { at: number; ready: boolean } | null = null;

async function openaiVoiceReady(): Promise<boolean> {
  if (keyCache && Date.now() - keyCache.at < 15_000) return keyCache.ready;
  let ready = false;
  try {
    const status = await getApi()?.get_key_status?.();
    ready = Boolean(status && (status as { openai?: boolean }).openai);
  } catch {
    ready = false;
  }
  keyCache = { at: Date.now(), ready };
  return ready;
}

function currentChoice(openaiReady: boolean): BackendChoice {
  return pickTranscriptionBackend({
    preference: getVoiceSettings().sttProvider,
    windowsSpeech: windowsSpeechUsable(),
    browserSpeech: browserSpeechUsable(),
    openaiReady,
  });
}

/**
 * Warm the engine the next mic press will use: spawn the Windows speech helper,
 * or mint an OpenAI token. Cheap and idempotent — call on hover / panel open.
 */
export function prewarmSpeech(): void {
  void (async () => {
    const choice = currentChoice(await openaiVoiceReady());
    if (choice.backend === "windows") void getApi()?.voice_win_stt_prewarm?.();
    if (choice.backend === "openai") prefetchRealtimeToken();
  })();
}

function langTag(): string {
  return (typeof navigator !== "undefined" && navigator.language) || "en-US";
}

const TARGET_RATE = 24000;
const MIN_SECONDS = 0.15;
const MIN_PEAK = 0.008;
/** Max time stop() waits for the last words before giving up. */
const STOP_DRAIN_MS = 3500;

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

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
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

/** Push-to-talk fallback: record until stop(), then Whisper REST. */
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
          setState("idle");
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
          handlers.onError?.(toSpeechError(String(result?.error || "Transcription failed")));
          setState("error");
          return;
        }
        const text = String(result.text || "").trim();
        if (text) handlers.onFinal?.(text);
        setState("idle");
      } catch (err) {
        handlers.onError?.(toSpeechError(err));
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

type RealtimeToken = { value: string; wsUrl: string; at: number };

/** Client secrets live 600s; reuse a prefetched one only while it is clearly fresh. */
const TOKEN_FRESH_MS = 240_000;
let tokenCache: RealtimeToken | null = null;
let tokenInflight: Promise<RealtimeToken> | null = null;

async function mintRealtimeToken(): Promise<RealtimeToken> {
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
    throw toSpeechError(`Could not start OpenAI listening: ${msg}`);
  }
  if (!token?.ok || !token.value) {
    throw toSpeechError(String(token?.error || "Could not start OpenAI listening"));
  }
  return {
    value: token.value,
    wsUrl: token.ws_url || "wss://api.openai.com/v1/realtime?intent=transcription",
    at: Date.now(),
  };
}

function prefetchRealtimeToken(): void {
  if (tokenCache && Date.now() - tokenCache.at < TOKEN_FRESH_MS) return;
  if (tokenInflight) return;
  tokenInflight = mintRealtimeToken()
    .then((t) => {
      tokenCache = t;
      return t;
    })
    .finally(() => {
      tokenInflight = null;
    });
  tokenInflight.catch(() => undefined);
}

/** One token per connection: a prefetched one if fresh, otherwise mint now. */
async function takeRealtimeToken(): Promise<RealtimeToken> {
  if (tokenInflight) {
    try {
      await tokenInflight;
    } catch {
      /* mint below */
    }
  }
  const cached = tokenCache;
  tokenCache = null;
  if (cached && Date.now() - cached.at < TOKEN_FRESH_MS) return cached;
  return mintRealtimeToken();
}

/**
 * Live STT via the OpenAI Realtime transcription WebSocket (GA).
 * The mic starts before the socket so the first words are buffered, not lost;
 * stop() commits the open turn and waits for its transcript.
 */
function createOpenAiStreamingTranscriptionSession(kind: "batch" | "streaming"): TranscriptionSession {
  let capture: PcmCapture | null = null;
  let ws: WebSocket | null = null;
  let handlers: TranscriptionHandlers = {};
  let state: TranscriptionState = "idle";
  let interim = "";
  /** Aborted or fully stopped — drop every event. */
  let closed = false;
  /** stop() pressed — no new audio, still deliver the last transcripts. */
  let stopping = false;
  let inSpeech = false;
  let awaitingCommit = false;
  let pendingTurns = 0;
  let drained: (() => void) | null = null;
  let backlog: string[] = [];

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

  const checkDrained = () => {
    if (stopping && pendingTurns <= 0 && !awaitingCommit && !inSpeech) {
      drained?.();
      drained = null;
    }
  };

  const sendAudio = (b64: string) => {
    if (closed || stopping) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "input_audio_buffer.append", audio: b64 }));
      return;
    }
    // ~85 ms per frame → cap the pre-connect buffer near 30 s.
    if (backlog.length < 360) backlog.push(b64);
  };

  const onMessage = (ev: MessageEvent) => {
    if (closed) return;
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(String(ev.data || "{}")) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = String(event.type || "");
    if (type === "input_audio_buffer.speech_started") {
      inSpeech = true;
      if (!stopping) handlers.onSpeechStarted?.();
      return;
    }
    if (type === "input_audio_buffer.speech_stopped") {
      inSpeech = false;
      if (!stopping) handlers.onSpeechStopped?.();
      checkDrained();
      return;
    }
    if (type === "input_audio_buffer.committed") {
      awaitingCommit = false;
      pendingTurns += 1;
      return;
    }
    if (type.endsWith("transcription.delta")) {
      const delta = String(event.delta || "");
      if (delta) {
        interim += delta;
        handlers.onInterim?.(interim);
      }
      return;
    }
    if (type.endsWith("transcription.completed")) {
      pendingTurns = Math.max(0, pendingTurns - 1);
      const finalText = String(event.transcript || interim || "").trim();
      interim = "";
      handlers.onInterim?.("");
      if (finalText) handlers.onFinal?.(finalText);
      checkDrained();
      return;
    }
    if (type.endsWith("transcription.failed")) {
      pendingTurns = Math.max(0, pendingTurns - 1);
      interim = "";
      handlers.onInterim?.("");
      checkDrained();
      return;
    }
    if (type === "error") {
      if (stopping) {
        // e.g. commit on an empty buffer while stopping — nothing left to wait for.
        awaitingCommit = false;
        checkDrained();
        return;
      }
      const err =
        typeof event.error === "object" && event.error
          ? String((event.error as { message?: string }).message || "OpenAI listening error")
          : "OpenAI listening error";
      handlers.onError?.(toSpeechError(err));
      setState("error");
    }
  };

  return {
    kind,
    async start(h = {}) {
      handlers = h;
      if (state === "listening" || state === "connecting") return;
      closed = false;
      stopping = false;
      inSpeech = false;
      awaitingCommit = false;
      pendingTurns = 0;
      interim = "";
      backlog = [];
      setState("connecting");

      const media = await requestMicAccess();
      try {
        capture = await startPcmCapture(media, (input, actualRate) => {
          const resampled = resampleLinear(input, actualRate, TARGET_RATE);
          sendAudio(int16ToBase64(floatTo16BitPcm(resampled)));
        });
      } catch (err) {
        media.getTracks().forEach((t) => t.stop());
        throw toSpeechError(err);
      }

      let token: RealtimeToken;
      try {
        token = await takeRealtimeToken();
      } catch (err) {
        cleanupAudio();
        throw err;
      }
      if (closed) {
        cleanupAudio();
        return;
      }

      // GA handshake only — the beta subprotocol routes to the retired Beta API.
      try {
        const socket = new WebSocket(token.wsUrl, ["realtime", `openai-insecure-api-key.${token.value}`]);
        ws = socket;
        await new Promise<void>((resolve, reject) => {
          const timer = window.setTimeout(() => reject(new Error("OpenAI listening timed out connecting")), 15_000);
          socket.onopen = () => {
            window.clearTimeout(timer);
            resolve();
          };
          socket.onerror = () => {
            window.clearTimeout(timer);
            reject(new Error("OpenAI listening could not connect"));
          };
          socket.onclose = (ev) => {
            window.clearTimeout(timer);
            if (!closed) {
              const detail = [ev.code, ev.reason].filter(Boolean).join(" ");
              reject(new Error(detail ? `OpenAI listening closed: ${detail}` : "OpenAI listening closed"));
            }
          };
        });
      } catch (err) {
        cleanupAudio();
        closeWs();
        throw toSpeechError(err);
      }
      if (closed || !ws) {
        cleanupAudio();
        closeWs();
        return;
      }

      ws.onmessage = onMessage;
      ws.onclose = (ev) => {
        if (closed) return;
        cleanupAudio();
        drained?.();
        drained = null;
        if (stopping) return;
        const detail = [ev.code, ev.reason].filter(Boolean).join(" ");
        if (detail && ev.code !== 1000) {
          handlers.onError?.(new SpeechError(`OpenAI listening disconnected (${detail}).`));
          setState("error");
          return;
        }
        setState("idle");
      };

      // Session config is bound to the ephemeral client secret — no session.update.
      for (const b64 of backlog) ws.send(JSON.stringify({ type: "input_audio_buffer.append", audio: b64 }));
      backlog = [];
      setState("listening");
    },
    async stop() {
      if (closed || !ws || ws.readyState !== WebSocket.OPEN) {
        this.abort();
        return;
      }
      stopping = true;
      cleanupAudio();
      if (inSpeech) {
        // Close the open turn now instead of waiting for server VAD silence.
        awaitingCommit = true;
        inSpeech = false;
        try {
          ws.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
        } catch {
          awaitingCommit = false;
        }
      }
      setState("transcribing");
      await new Promise<void>((resolve) => {
        drained = resolve;
        checkDrained();
        window.setTimeout(resolve, STOP_DRAIN_MS);
      });
      drained = null;
      closed = true;
      closeWs();
      interim = "";
      setState("idle");
    },
    abort() {
      closed = true;
      drained?.();
      drained = null;
      closeWs();
      cleanupAudio();
      interim = "";
      setState("idle");
    },
  };
}

/** OpenAI dictation: live words via Realtime; Whisper batch if Realtime will not start. */
function createOpenAiDictationSession(): TranscriptionSession {
  let inner: TranscriptionSession = createOpenAiStreamingTranscriptionSession("batch");
  return {
    kind: "batch",
    async start(h = {}) {
      try {
        await inner.start(h);
      } catch (err) {
        const e = toSpeechError(err);
        if (e.action) throw e;
        inner = createOpenAiBatchTranscriptionSession();
        await inner.start(h);
      }
    },
    stop: () => inner.stop(),
    abort: () => inner.abort(),
  };
}

/** Windows dictation through the desktop backend: long-polls interim / final words. */
function createWindowsTranscriptionSession(kind: "batch" | "streaming"): TranscriptionSession {
  let sid = "";
  let cursor = 0;
  let handlers: TranscriptionHandlers = {};
  /** Aborted — drop everything. */
  let closed = false;
  /** stop() pressed — no interim, still deliver the last finals. */
  let stopping = false;
  let ended: (() => void) | null = null;
  /** The last final, as plain words: a guess for that phrase that lands after it is stale. */
  let lastFinal = "";

  const setState = (next: TranscriptionState) => handlers.onStateChange?.(next);

  const finish = () => {
    ended?.();
    ended = null;
  };

  const dispatch = (ev: WinSttEvent) => {
    if (closed) return;
    switch (ev.t) {
      case "interim": {
        if (stopping) return;
        const text = String(ev.text || "");
        // Windows raises guesses and results on separate events, so a guess for the
        // phrase just confirmed can arrive after it and would show those words twice.
        if (lastFinal && lastFinal.startsWith(plainWords(text))) return;
        lastFinal = "";
        handlers.onInterim?.(text);
        return;
      }
      case "final": {
        handlers.onInterim?.("");
        const text = String(ev.text || "").trim();
        lastFinal = plainWords(text);
        if (text) handlers.onFinal?.(text);
        return;
      }
      case "speech_started":
        if (!stopping) handlers.onSpeechStarted?.();
        return;
      case "speech_stopped":
        if (!stopping) handlers.onSpeechStopped?.();
        return;
      case "error":
        if (stopping) return;
        handlers.onError?.(
          new SpeechError(String(ev.message || "Windows speech failed"), actionForCode(ev.code), ev.code),
        );
        setState("error");
        return;
      default:
        return;
    }
  };

  const pump = async (mySid: string) => {
    const api = getApi();
    let failures = 0;
    while (sid === mySid && !closed) {
      let res: Awaited<ReturnType<NonNullable<NonNullable<typeof api>["voice_win_stt_poll"]>>> | undefined;
      try {
        res = await api?.voice_win_stt_poll?.(mySid, cursor, 400);
        failures = 0;
      } catch {
        failures += 1;
        if (failures > 10) break;
        await sleep(250);
        continue;
      }
      if (sid !== mySid || closed || !res) break;
      cursor = typeof res.cursor === "number" ? res.cursor : cursor;
      for (const ev of res.events || []) dispatch(ev);
      if (res.active === false) {
        const crashed = (res.events || []).some((ev) => ev.t === "exit");
        if (crashed && !stopping && !closed) {
          handlers.onError?.(new SpeechError("Windows speech stopped unexpectedly. Press the mic to try again."));
          setState("error");
        }
        break;
      }
    }
    if (sid === mySid) {
      finish();
      if (!stopping && !closed) setState("idle");
    }
  };

  return {
    kind,
    async start(h = {}) {
      handlers = h;
      closed = false;
      stopping = false;
      const api = getApi();
      if (!api?.voice_win_stt_start) throw new SpeechError("Windows speech is not available here.");
      setState("connecting");
      const res = await api.voice_win_stt_start(langTag());
      if (!res?.ok || !res.session) {
        throw new SpeechError(String(res?.error || "Windows speech failed to start"), actionForCode(res?.code), res?.code);
      }
      if (closed) {
        void api.voice_win_stt_cancel?.(res.session);
        return;
      }
      lastFinal = "";
      sid = res.session;
      cursor = typeof res.cursor === "number" ? res.cursor : 0;
      setState("listening");
      void pump(sid);
    },
    async stop() {
      const running = sid;
      if (!running || closed) {
        this.abort();
        return;
      }
      stopping = true;
      setState("transcribing");
      const done = new Promise<void>((resolve) => {
        ended = resolve;
      });
      void getApi()?.voice_win_stt_stop?.(running);
      await Promise.race([done, sleep(STOP_DRAIN_MS)]);
      ended = null;
      closed = true;
      sid = "";
      setState("idle");
    },
    abort() {
      const running = sid;
      closed = true;
      sid = "";
      finish();
      if (running) void getApi()?.voice_win_stt_cancel?.(running);
      setState("idle");
    },
  };
}

/** Lowercase words without punctuation, so "make a door" matches the final "Make a door." */
function plainWords(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s']/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Android's recognizer repeats earlier phrases in continuous mode; one phrase per run there. */
function isAndroid(): boolean {
  return typeof navigator !== "undefined" && /Android/i.test(navigator.userAgent || "");
}

function webSpeechMessage(code: string): SpeechError {
  if (code === "not-allowed" || code === "service-not-allowed") {
    return new SpeechError("The browser blocked the microphone. Allow it for this page, then try again.");
  }
  if (code === "audio-capture") return new SpeechError("No microphone found. Plug one in and try again.");
  if (code === "network") return new SpeechError("Browser speech can't reach its speech service. Check your connection.");
  if (code === "language-not-supported") return new SpeechError("Browser speech doesn't support this language.");
  return new SpeechError(`Browser speech failed (${code || "unknown"}).`);
}

function createWebSpeechTranscriptionSession(kind: "batch" | "streaming"): TranscriptionSession {
  const Ctor = getSpeechRecognitionCtor();
  let rec: BrowserSpeechRec | null = null;
  let handlers: TranscriptionHandlers = {};
  let state: TranscriptionState = "idle";
  let closed = false;

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
    // Android: single-phrase runs; onend below starts the next one.
    speech.continuous = !isAndroid();
    speech.interimResults = true;
    speech.lang = langTag();
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
      handlers.onError?.(webSpeechMessage(code));
      setState("error");
    };
    speech.onend = () => {
      // Chrome ends continuous recognition on its own after a pause — keep listening.
      if (closed || state !== "listening") return;
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
      if (!Ctor) throw new SpeechError("Browser speech is not available on this device.");
      setState("connecting");
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
      closed = true;
      const running = rec;
      rec = null;
      if (!running) {
        setState("idle");
        return;
      }
      setState("transcribing");
      // stop() (not abort) flushes the pending phrase as a final before onend.
      await new Promise<void>((resolve) => {
        running.onend = () => resolve();
        try {
          running.stop();
        } catch {
          resolve();
        }
        window.setTimeout(resolve, STOP_DRAIN_MS);
      });
      setState("idle");
    },
    abort() {
      closed = true;
      drop();
      setState("idle");
    },
  };
}

function makeEngine(backend: SttBackend, kind: "batch" | "streaming"): TranscriptionSession {
  if (backend === "windows") return createWindowsTranscriptionSession(kind);
  if (backend === "webspeech") return createWebSpeechTranscriptionSession(kind);
  return kind === "batch" ? createOpenAiDictationSession() : createOpenAiStreamingTranscriptionSession(kind);
}

function noEngineError(choice: BackendChoice): SpeechError {
  return new SpeechError(
    choice.openaiMissingKey
      ? "Listen is set to OpenAI, but no OpenAI key is saved."
      : "No speech engine here. Add an OpenAI key to talk to Ducky.",
    "openai_key",
  );
}

/**
 * Pick the engine per start. OpenAI without a key → the system engine plus a
 * notice (never a silent swap). Windows speech off + OpenAI key → OpenAI.
 */
function wrapSession(kind: "batch" | "streaming"): TranscriptionSession {
  let inner: TranscriptionSession | null = null;
  let gen = 0;
  return {
    kind,
    async start(handlers = {}) {
      inner?.abort();
      inner = null;
      const my = ++gen;
      const openaiReady = await openaiVoiceReady();
      if (my !== gen) return;
      const choice = currentChoice(openaiReady);
      if (choice.backend === "none") throw noEngineError(choice);
      if (choice.openaiMissingKey) {
        handlers.onNotice?.(
          `No OpenAI key saved — using ${choice.backend === "windows" ? "Windows" : "browser"} speech.`,
        );
      }
      const engine = makeEngine(choice.backend, kind);
      inner = engine;
      try {
        await engine.start(handlers);
      } catch (err) {
        const e = toSpeechError(err);
        if (my !== gen) return;
        if (choice.backend === "windows" && openaiReady && e.action === "speech_privacy") {
          handlers.onNotice?.("Windows speech is off — using OpenAI.");
          const fallback = makeEngine("openai", kind);
          inner = fallback;
          await fallback.start(handlers);
          return;
        }
        inner = null;
        throw e;
      }
    },
    async stop() {
      await inner?.stop();
    },
    abort() {
      gen += 1;
      inner?.abort();
      inner = null;
    },
  };
}

/** Dictation — words land in the chat box; never sends. */
export function createBatchTranscriptionSession(): TranscriptionSession {
  return wrapSession("batch");
}

/** Live voice — continuous listening with end-of-turn finals. */
export function createStreamingTranscriptionSession(): TranscriptionSession {
  return wrapSession("streaming");
}

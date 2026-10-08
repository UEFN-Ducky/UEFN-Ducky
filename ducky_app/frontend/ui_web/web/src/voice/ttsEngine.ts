/**
 * Incremental TTS engine: sentence queue → speechSynthesis (builtin) or plugin audio.
 * Supports pause / resume / restart and read-along char progress.
 */

import { getApi } from "../hooks/usePanelApi";
import { applyOutputDevice, effectivePlaybackVolume, getAudioSettings } from "./audioSettings";
import { SentenceQueue } from "./sentenceQueue";
import { pickVoiceForText, voiceLangTag } from "./voiceMatch";

export type TtsState = "idle" | "speaking" | "paused";

export type TtsProgress = {
  state: TtsState;
  /** Full text passed to speak() (for matching a message bubble). */
  sourceText: string;
  /** Spoken form used for read-along (joined sentence chunks). */
  spokenText: string;
  /** Char index into spokenText. */
  charIndex: number;
  /**
   * True while a plugin voice is synthesizing (often downloading the model
   * on first use, e.g. Piper). UI should show a clear "loading voice" state.
   */
  loading: boolean;
  loadingMessage?: string;
  error?: string;
  errorCode?: string;
};

export type TtsVoiceInfo = {
  id: string;
  label: string;
  kind: "builtin" | "plugin";
  pluginId?: string;
  voiceId?: string;
};

type Listener = (state: TtsState) => void;
type ProgressListener = (progress: TtsProgress) => void;

let currentVoiceId = "";
let currentRate = 1;
let speaking = false;
let queue = new SentenceQueue();
let utterQueue: string[] = [];
let utterOffsets: number[] = [];
let playing = false;
let audioEl: HTMLAudioElement | null = null;
let finishPlayback: (() => void) | null = null;
const listeners = new Set<Listener>();
const progressListeners = new Set<ProgressListener>();
let lastSpokenText = "";
let activeSourceText = "";
let cachedProgress: TtsProgress | null = null;
let activeSpokenText = "";
let highlightIndex = 0;
let playbackState: TtsState = "idle";
let pauseGate: Promise<void> | null = null;
let pauseResolve: (() => void) | null = null;
let sessionGen = 0;
/** Plugin synth in flight (download + generate). */
let loadingVoice = false;
let loadingMessage = "";
let playbackError = "";
let playbackErrorCode = "";
let windowsVoices: TtsVoiceInfo[] = [];
/** True only after the user (or UI) calls pause() — browser auto-pauses must still resume. */
let userPaused = false;
let synthWatchdog: ReturnType<typeof setInterval> | null = null;

/** Multi-speaker queue: each item is a full reply with its own voice. */
export type TtsUtterance = {
  text: string;
  voiceId: string;
  speaker?: string;
  /** Playback speed multiplier for this utterance (1 = normal). */
  rate?: number;
};

/** Clamp a speed multiplier to a safe playback range. */
function clampRate(n: number): number {
  return Number.isFinite(n) ? Math.max(0.25, Math.min(4, n)) : 1;
}

let utteranceQueue: TtsUtterance[] = [];
let utteranceDraining = false;
let currentSpeaker = "";
type UtteranceListener = (info: { speaker: string; remaining: number }) => void;
const utteranceListeners = new Set<UtteranceListener>();

function emitUtteranceInfo() {
  const info = { speaker: currentSpeaker, remaining: utteranceQueue.length };
  for (const fn of utteranceListeners) fn(info);
}

function emit(state: TtsState) {
  playbackState = state;
  speaking = state === "speaking";
  for (const fn of listeners) fn(state);
  emitProgress();
}

function emitProgress() {
  const progress: TtsProgress = {
    state: playbackState,
    sourceText: activeSourceText,
    spokenText: activeSpokenText,
    charIndex: highlightIndex,
    loading: loadingVoice,
    loadingMessage,
    error: playbackError,
    errorCode: playbackErrorCode,
  };
  for (const fn of progressListeners) fn(progress);
}

function setLoadingVoice(on: boolean, message = "Preparing voice… First use may download voice files.") {
  const nextMessage = on ? message : "";
  if (loadingVoice === on && loadingMessage === nextMessage) return;
  loadingVoice = on;
  loadingMessage = nextMessage;
  emitProgress();
}

function clearPauseGate() {
  pauseResolve?.();
  pauseGate = null;
  pauseResolve = null;
}

/** Chromium/WebView2: once paused, later speak() utterances often start paused until resume(). */
function clearSynthPausedLatch() {
  if (typeof speechSynthesis === "undefined") return;
  try {
    if (speechSynthesis.paused) speechSynthesis.resume();
  } catch {
    /* ignore */
  }
}

function stopSynthWatchdog() {
  if (synthWatchdog != null) {
    clearInterval(synthWatchdog);
    synthWatchdog = null;
  }
}

/**
 * Keep builtin speech moving: Chromium freezes on the first word (or ~15s in) by
 * flipping speechSynthesis.paused without firing pause events we track.
 */
function startSynthWatchdog(gen: number) {
  stopSynthWatchdog();
  if (typeof speechSynthesis === "undefined") return;
  synthWatchdog = setInterval(() => {
    if (gen !== sessionGen || userPaused) {
      if (gen !== sessionGen) stopSynthWatchdog();
      return;
    }
    try {
      if (speechSynthesis.speaking && speechSynthesis.paused) {
        speechSynthesis.resume();
      }
    } catch {
      /* ignore */
    }
  }, 200);
}

function waitIfPaused(): Promise<void> {
  if (playbackState !== "paused") return Promise.resolve();
  if (!pauseGate) {
    pauseGate = new Promise((resolve) => {
      pauseResolve = resolve;
    });
  }
  return pauseGate;
}

function parseVoiceId(voiceId: string): { kind: "builtin" | "plugin"; pluginId?: string; name: string } {
  const raw = (voiceId || "").trim();
  if (raw.startsWith("plugin:")) {
    const rest = raw.slice("plugin:".length);
    const colon = rest.indexOf(":");
    if (colon > 0) {
      return { kind: "plugin", pluginId: rest.slice(0, colon), name: rest.slice(colon + 1) };
    }
    return { kind: "plugin", pluginId: rest, name: "" };
  }
  if (raw.startsWith("builtin:")) {
    return { kind: "builtin", name: raw.slice("builtin:".length) };
  }
  return { kind: "builtin", name: raw };
}

function findBuiltinVoice(name: string): SpeechSynthesisVoice | null {
  if (typeof speechSynthesis === "undefined") return null;
  const voices = speechSynthesis.getVoices();
  if (!name) return voices[0] || null;
  return (
    voices.find((v) => v.name === name || v.voiceURI === name) ||
    voices.find((v) => v.name.toLowerCase().includes(name.toLowerCase())) ||
    null
  );
}

async function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

type Synth = { url: string; revoke: () => void } | null;

async function synthesizePlugin(pluginId: string, voiceId: string, text: string, gen: number): Promise<Synth> {
  const api = getApi();
  const failed = (message: string) => {
    if (gen === sessionGen) {
      playbackError = `${message} Using the default system voice.`;
      emitProgress();
    }
    return null;
  };
  if (!api?.plugin_tts_start || !api?.plugin_tts_poll) return failed("The selected voice is unavailable.");
  try {
    const started = await api.plugin_tts_start(pluginId, text, voiceId);
    if (!started?.ok || !started.job_id) return failed(started?.error || "The selected voice could not start.");
    // Piper first-use can download tens of MB; keep polling longer than a normal synth.
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      if (gen !== sessionGen) return null;
      await sleep(100);
      const polled = await api.plugin_tts_poll(String(started.job_id));
      if (polled?.pending) continue;
      if (!polled?.ok) return failed(polled?.error || "The selected voice could not be prepared.");
      const b64 = String((polled as { audio_base64?: string }).audio_base64 || "");
      const mime = String((polled as { mime?: string }).mime || "audio/mpeg");
      if (!b64) return failed("The selected voice returned no audio.");
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: mime });
      const url = URL.createObjectURL(blob);
      return { url, revoke: () => URL.revokeObjectURL(url) };
    }
    return failed("The voice download or preparation took too long.");
  } catch (err) {
    return failed(err instanceof Error ? err.message : "The selected voice failed.");
  }
}


/** Native synthesis produces WAV on a worker; the browser owns playback and devices. */
async function waitWindowsJob(jobId: string, gen?: number) {
  const api = getApi();
  const deadline = Date.now() + 65_000;
  while (Date.now() < deadline) {
    if (gen != null && gen !== sessionGen) return null;
    const result = await api?.voice_win_tts_poll?.(jobId);
    if (gen != null && gen !== sessionGen) return null;
    if (result?.pending) { await sleep(100); continue; }
    if (!result?.ok) {
      if (gen != null) playbackErrorCode = result?.code || "";
      throw new Error(result?.error || "Windows Speech is unavailable.");
    }
    return result;
  }
  throw new Error("Windows Speech took too long to prepare.");
}

function audioFromBase64(b64: string, mime: string): NonNullable<Synth> {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  return { url, revoke: () => URL.revokeObjectURL(url) };
}

async function playWindows(text: string, voiceName: string, offset: number, gen: number) {
  let synth: Synth = null;
  setLoadingVoice(true, "Preparing Windows Speech…");
  try {
    const started = await getApi()?.voice_win_tts_start?.(text, voiceName);
    if (gen !== sessionGen) return;
    if (!started?.ok || !started.job_id) throw new Error(started?.error || "Windows Speech is unavailable.");
    const result = await waitWindowsJob(started.job_id, gen);
    if (!result || gen !== sessionGen) return;
    if (!result.audio_base64) throw new Error("Windows Speech returned no audio.");
    synth = audioFromBase64(result.audio_base64, result.mime || "audio/wav");
  } finally {
    if (gen === sessionGen) setLoadingVoice(false);
  }
  try {
    if (gen === sessionGen) await waitIfPaused();
    if (gen === sessionGen && synth) await playUrl(synth.url, text, offset, gen);
  } finally { synth?.revoke(); }
}

/** Next sentence's plugin audio, synthesized while the current one plays. */
type Prefetch = { text: string; key: string; gen: number; done: boolean; promise: Promise<Synth> };
let prefetched: Prefetch | null = null;

function dropPrefetch() {
  const p = prefetched;
  prefetched = null;
  void p?.promise.then((synth) => synth?.revoke());
}

function prefetchNext(gen: number) {
  const nextText = utterQueue[0];
  if (!nextText || gen !== sessionGen) return;
  const parsed = parseVoiceId(pickVoiceForText(nextText, currentVoiceId));
  if (parsed.kind !== "plugin" || !parsed.pluginId) return;
  const key = `${parsed.pluginId}:${parsed.name}`;
  if (prefetched && prefetched.text === nextText && prefetched.key === key && prefetched.gen === gen) return;
  dropPrefetch();
  const entry: Prefetch = {
    text: nextText,
    key,
    gen,
    done: false,
    promise: synthesizePlugin(parsed.pluginId, parsed.name, nextText, gen),
  };
  void entry.promise.then(() => {
    entry.done = true;
  });
  prefetched = entry;
}

/** Use the prefetched audio when it matches, otherwise synthesize now. Shows "loading" only while waiting. */
async function takeSynth(pluginId: string, name: string, text: string, gen: number): Promise<Synth> {
  const key = `${pluginId}:${name}`;
  let entry = prefetched;
  if (entry && (entry.text !== text || entry.key !== key || entry.gen !== gen)) {
    dropPrefetch();
    entry = null;
  }
  prefetched = null;
  const promise = entry ? entry.promise : synthesizePlugin(pluginId, name, text, gen);
  const wait = !entry?.done;
  if (wait) setLoadingVoice(true);
  try {
    return await promise;
  } finally {
    if (wait && gen === sessionGen) setLoadingVoice(false);
  }
}

function ttsOutVolume(): number {
  const s = getAudioSettings();
  return effectivePlaybackVolume(s.ttsVolume, s.audioMuted);
}

/** Map sentence chunks back onto concatenated spoken text for highlight offsets. */
export function chunkOffsets(chunks: string[]): number[] {
  const offsets: number[] = [];
  let at = 0;
  for (const chunk of chunks) {
    offsets.push(at);
    at += chunk.length + 1; // +1 for the space join used when speaking a full message
  }
  return offsets;
}

async function playBuiltin(text: string, voiceName: string, offset: number, gen: number): Promise<void> {
  if (typeof window !== "undefined" && window.pywebview && getApi()?.voice_win_tts_start) {
    await playWindows(text, voiceName, offset, gen);
    return;
  }
  return new Promise((resolve) => {
    if (typeof speechSynthesis === "undefined") {
      playbackError = "Speech playback is unavailable on this device.";
      resolve();
      return;
    }
    const vol = ttsOutVolume();
    if (vol <= 0) {
      resolve();
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    const voice = findBuiltinVoice(voiceName);
    if (voice) {
      u.voice = voice;
      if (voice.lang) u.lang = voice.lang;
    } else {
      const tag = voiceLangTag(voiceName);
      if (tag) u.lang = tag;
    }
    u.volume = vol;
    u.rate = clampRate(currentRate);
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      if (finishPlayback === done) finishPlayback = null;
      stopSynthWatchdog();
      if (gen === sessionGen) {
        highlightIndex = offset + text.length;
        emitProgress();
      }
      resolve();
    };
    finishPlayback = done;
    u.onboundary = (ev) => {
      if (gen !== sessionGen) return;
      if (typeof ev.charIndex === "number") {
        highlightIndex = offset + ev.charIndex;
        emitProgress();
      }
      // First boundary often fires then Chromium parks paused — kick it.
      if (!userPaused && speechSynthesis.paused) {
        try {
          speechSynthesis.resume();
        } catch {
          /* ignore */
        }
      }
    };
    u.onstart = () => {
      if (gen !== sessionGen || userPaused) return;
      try {
        if (speechSynthesis.paused) speechSynthesis.resume();
      } catch {
        /* ignore */
      }
    };
    u.onend = done;
    u.onerror = (ev) => {
      if (gen === sessionGen && ev.error !== "canceled" && ev.error !== "interrupted") {
        playbackError = `Speech playback failed (${ev.error}). Try another voice.`;
      }
      done();
    };
    clearSynthPausedLatch();
    startSynthWatchdog(gen);
    speechSynthesis.speak(u);
    // Some WebViews queue the utterance already-paused after a prior cancel/pause.
    queueMicrotask(() => {
      if (gen !== sessionGen || userPaused || settled) return;
      try {
        if (speechSynthesis.paused) speechSynthesis.resume();
      } catch {
        /* ignore */
      }
    });
  });
}

function playUrl(url: string, text: string, offset: number, gen: number): Promise<void> {
  return new Promise((resolve) => {
    if (audioEl) {
      try {
        audioEl.pause();
      } catch {
        /* ignore */
      }
    }
    const vol = ttsOutVolume();
    if (vol <= 0) {
      resolve();
      return;
    }
    const el = new Audio(url);
    audioEl = el;
    el.volume = vol;
    // Speed up/slow down plugin audio while keeping pitch natural.
    el.playbackRate = clampRate(currentRate);
    el.preservesPitch = true;
    const done = () => {
      if (finishPlayback === done) finishPlayback = null;
      if (gen === sessionGen) {
        highlightIndex = offset + text.length;
        emitProgress();
      }
      if (audioEl === el) audioEl = null;
      resolve();
    };
    finishPlayback = done;
    el.ontimeupdate = () => {
      if (gen !== sessionGen || !el.duration) return;
      const t = Math.max(0, Math.min(1, el.currentTime / el.duration));
      highlightIndex = offset + Math.floor(t * text.length);
      emitProgress();
    };
    el.onended = done;
    el.onerror = () => {
      if (gen === sessionGen) playbackError = "Audio playback failed. Check the selected speakers.";
      done();
    };
    void applyOutputDevice(el)
      .then(async () => {
        if (gen === sessionGen) await waitIfPaused();
        if (gen !== sessionGen) { done(); return; }
        await el.play();
      })
      .catch((err: unknown) => {
        if (gen === sessionGen) playbackError = err instanceof Error ? err.message : "Audio playback failed.";
        done();
      });
  });
}

async function drain(): Promise<void> {
  if (playing) return;
  playing = true;
  const gen = sessionGen;
  emit("speaking");
  try {
    while (utterQueue.length) {
      if (gen !== sessionGen) break;
      await waitIfPaused();
      if (gen !== sessionGen) break;
      const text = utterQueue.shift()!;
      const offset = utterOffsets.shift() ?? 0;
      lastSpokenText = text;
      highlightIndex = offset;
      emitProgress();
      // Per-chunk only — never writes defaultVoice / currentVoiceId.
      const parsed = parseVoiceId(pickVoiceForText(text, currentVoiceId));
      if (parsed.kind === "plugin" && parsed.pluginId) {
        const synth = await takeSynth(parsed.pluginId, parsed.name, text, gen);
        // Paused while the audio was downloading — hold it until resume.
        if (gen === sessionGen) await waitIfPaused();
        if (gen !== sessionGen) {
          synth?.revoke();
          break;
        }
        if (synth) {
          prefetchNext(gen);
          await playUrl(synth.url, text, offset, gen);
          synth.revoke();
          continue;
        }
      }
      await playBuiltin(text, parsed.kind === "builtin" ? parsed.name : "", offset, gen);
    }
  } catch (err) {
    if (gen === sessionGen) {
      playbackError = err instanceof Error ? err.message : "Speech playback failed.";
      utterQueue = [];
      utterOffsets = [];
    }
  } finally {
    if (gen === sessionGen) playing = false;
    if (gen === sessionGen && !utterQueue.length) {
      if (!playbackError) activeSourceText = "";
      activeSpokenText = "";
      highlightIndex = 0;
      emit("idle");
    }
  }
}

export const ttsEngine = {
  setVoice(voiceId: string) {
    currentVoiceId = (voiceId || "").trim();
  },

  getVoice(): string {
    return currentVoiceId;
  },

  setRate(rate: number) {
    currentRate = clampRate(rate);
    // Live-apply to whoever is talking right now (plugin audio). Queued speakers too.
    if (audioEl) {
      try {
        audioEl.playbackRate = currentRate;
        audioEl.preservesPitch = true;
      } catch {
        /* ignore */
      }
    }
    for (const u of utteranceQueue) u.rate = currentRate;
  },

  getRate(): number {
    return currentRate;
  },

  isSpeaking(): boolean {
    return speaking || playing || playbackState === "paused";
  },

  getState(): TtsState {
    return playbackState;
  },

  /** Stable snapshot: the same object comes back until a field actually changes
   *  (useSyncExternalStore in every assistant bubble relies on that). */
  getProgress(): TtsProgress {
    const c = cachedProgress;
    if (
      c &&
      c.state === playbackState &&
      c.sourceText === activeSourceText &&
      c.spokenText === activeSpokenText &&
      c.charIndex === highlightIndex &&
      c.loading === loadingVoice &&
      c.loadingMessage === loadingMessage &&
      c.error === playbackError &&
      c.errorCode === playbackErrorCode
    ) {
      return c;
    }
    cachedProgress = {
      state: playbackState,
      sourceText: activeSourceText,
      spokenText: activeSpokenText,
      charIndex: highlightIndex,
      loading: loadingVoice,
    loadingMessage,
    error: playbackError,
    errorCode: playbackErrorCode,
    };
    return cachedProgress;
  },

  getLastSpoken(): string {
    return lastSpokenText;
  },

  onStateChange(fn: Listener): () => void {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },

  onProgress(fn: ProgressListener): () => void {
    progressListeners.add(fn);
    fn(this.getProgress());
    return () => progressListeners.delete(fn);
  },

  listBuiltinVoices(): TtsVoiceInfo[] {
    if (windowsVoices.length) return windowsVoices;
    if (typeof speechSynthesis === "undefined") return [];
    return speechSynthesis.getVoices().map((v) => ({
      id: `builtin:${v.name}`,
      label: `${v.name}${v.lang ? ` (${v.lang})` : ""}`,
      kind: "builtin" as const,
    }));
  },

  /** Ensure voices are loaded (Chrome populates async). */
  async whenVoicesReady(): Promise<TtsVoiceInfo[]> {
    const api = typeof window !== "undefined" && window.pywebview ? getApi() : null;
    if (api?.voice_win_tts_voices_start && api.voice_win_tts_poll) {
      try {
        const started = await api.voice_win_tts_voices_start();
        if (!started.ok || !started.job_id) return [];
        const result = await waitWindowsJob(started.job_id);
        windowsVoices = (result?.voices || []).map((v) => ({
          id: `builtin:${v.id}`, label: `${v.label}${v.lang ? ` (${v.lang})` : ""}`, kind: "builtin",
        }));
        return windowsVoices;
      } catch { return []; }
    }
    return new Promise((resolve) => {
      const list = () => resolve(this.listBuiltinVoices());
      if (typeof speechSynthesis === "undefined") {
        resolve([]);
        return;
      }
      const existing = speechSynthesis.getVoices();
      if (existing.length) {
        list();
        return;
      }
      const onChange = () => {
        speechSynthesis.removeEventListener("voiceschanged", onChange);
        list();
      };
      speechSynthesis.addEventListener("voiceschanged", onChange);
      window.setTimeout(list, 800);
    });
  },

  enqueue(delta: string) {
    if (getAudioSettings().audioMuted) return;
    const sentences = queue.enqueue(delta);
    if (sentences.length) {
      for (const s of sentences) {
        utterOffsets.push(0);
        utterQueue.push(s);
      }
      if (playbackState === "idle") {
        activeSourceText = "";
        activeSpokenText = "";
      }
      if (playing && !prefetched) prefetchNext(sessionGen);
      void drain();
    }
  },

  /** Speak a full string (summary / replay / message button). Cancels any queue. */
  speak(text: string, voiceId?: string, rate?: number) {
    if (getAudioSettings().audioMuted) return;
    utteranceQueue = [];
    utteranceDraining = false;
    currentSpeaker = "";
    emitUtteranceInfo();
    if (voiceId != null) this.setVoice(voiceId);
    if (rate != null) this.setRate(rate);
    this.cancel();
    const cleaned = (text || "").trim();
    if (!cleaned) return;
    sessionGen += 1;
    userPaused = false;
    activeSourceText = cleaned;
    highlightIndex = 0;
    queue.clear();
    utterQueue = [];
    utterOffsets = [];
    const sentences = queue.enqueue(cleaned);
    const finals = sentences.length ? sentences : queue.flush();
    const chunks = finals.length ? finals : [cleaned];
    utterQueue.push(...chunks);
    activeSpokenText = chunks.join(" ");
    utterOffsets = chunkOffsets(chunks);
    void drain();
  },

  /**
   * Queue a full reply with its own voice (group roundtable).
   * Plays sequentially; does not cancel the current speaker.
   */
  enqueueUtterance(text: string, voiceId?: string, speaker?: string, rate?: number) {
    if (getAudioSettings().audioMuted) return;
    const cleaned = (text || "").trim();
    if (!cleaned) return;
    utteranceQueue.push({
      text: cleaned,
      voiceId: (voiceId || currentVoiceId || "").trim(),
      speaker: (speaker || "").trim(),
      rate: rate != null ? clampRate(rate) : currentRate,
    });
    emitUtteranceInfo();
    void this._drainUtterances();
  },

  /** Skip the current multi-speaker utterance and play the next (if any). */
  skipUtterance() {
    if (!utteranceDraining && !utteranceQueue.length && !this.isSpeaking()) return;
    // Cancel in-flight sentence playback without wiping the remaining queue.
    sessionGen += 1;
    dropPrefetch();
    clearPauseGate();
    queue.clear();
    utterQueue = [];
    utterOffsets = [];
    if (typeof speechSynthesis !== "undefined") {
      try {
        speechSynthesis.cancel();
      } catch {
        /* ignore */
      }
    }
    if (audioEl) {
      try {
        audioEl.pause();
        audioEl.src = "";
      } catch {
        /* ignore */
      }
      audioEl = null;
    }
    playing = false;
    currentSpeaker = "";
    emit("idle");
    emitUtteranceInfo();
    void this._drainUtterances();
  },

  getUtteranceQueueLength(): number {
    return utteranceQueue.length + (utteranceDraining ? 1 : 0);
  },

  getCurrentSpeaker(): string {
    return currentSpeaker;
  },

  onUtteranceChange(fn: UtteranceListener): () => void {
    utteranceListeners.add(fn);
    fn({ speaker: currentSpeaker, remaining: utteranceQueue.length });
    return () => utteranceListeners.delete(fn);
  },

  async _drainUtterances() {
    if (utteranceDraining) return;
    utteranceDraining = true;
    try {
      while (utteranceQueue.length) {
        const next = utteranceQueue.shift()!;
        currentSpeaker = next.speaker || "";
        emitUtteranceInfo();
        await new Promise<void>((resolve) => {
          const unsub = this.onStateChange((s) => {
            if (s === "idle") {
              unsub();
              resolve();
            }
          });
          // speak() clears utteranceQueue — call internal play path instead.
          this._speakOne(next.text, next.voiceId, next.rate);
          if (!this.isSpeaking()) {
            unsub();
            resolve();
          }
        });
      }
    } finally {
      utteranceDraining = false;
      currentSpeaker = "";
      emitUtteranceInfo();
    }
  },

  /** Internal: speak without clearing the multi-speaker utterance queue. */
  _speakOne(text: string, voiceId?: string, rate?: number) {
    playbackError = "";
    playbackErrorCode = "";
    loadingVoice = false;
    loadingMessage = "";
    if (voiceId != null) this.setVoice(voiceId);
    if (rate != null) this.setRate(rate);
    sessionGen += 1;
    finishPlayback?.();
    userPaused = false;
    stopSynthWatchdog();
    dropPrefetch();
    clearPauseGate();
    if (typeof speechSynthesis !== "undefined") {
      try {
        speechSynthesis.cancel();
      } catch {
        /* ignore */
      }
      clearSynthPausedLatch();
    }
    if (audioEl) {
      try {
        audioEl.pause();
        audioEl.src = "";
      } catch {
        /* ignore */
      }
      audioEl = null;
    }
    playing = false;
    const cleaned = (text || "").trim();
    if (!cleaned) {
      emit("idle");
      return;
    }
    activeSourceText = cleaned;
    highlightIndex = 0;
    queue.clear();
    utterQueue = [];
    utterOffsets = [];
    const sentences = queue.enqueue(cleaned);
    const finals = sentences.length ? sentences : queue.flush();
    const chunks = finals.length ? finals : [cleaned];
    utterQueue.push(...chunks);
    activeSpokenText = chunks.join(" ");
    utterOffsets = chunkOffsets(chunks);
    void drain();
  },

  flush() {
    const sentences = queue.flush();
    if (sentences.length) {
      for (const s of sentences) {
        utterOffsets.push(0);
        utterQueue.push(s);
      }
      void drain();
    }
  },

  pause() {
    if (playbackState !== "speaking") return;
    userPaused = true;
    playbackState = "paused";
    speaking = false;
    try {
      if (typeof speechSynthesis !== "undefined") speechSynthesis.pause();
    } catch {
      /* ignore */
    }
    try {
      audioEl?.pause();
    } catch {
      /* ignore */
    }
    emit("paused");
  },

  resume() {
    if (playbackState !== "paused") return;
    userPaused = false;
    playbackState = "speaking";
    speaking = true;
    try {
      if (typeof speechSynthesis !== "undefined") speechSynthesis.resume();
    } catch {
      /* ignore */
    }
    void audioEl?.play().catch(() => undefined);
    clearPauseGate();
    emit("speaking");
    if (!playing && utterQueue.length) void drain();
  },

  /** Restart the active (or last) full speak() text from the beginning. */
  restart(voiceId?: string, rate?: number) {
    const text = activeSourceText || lastSpokenText;
    if (!text) return;
    this.speak(text, voiceId ?? currentVoiceId, rate ?? currentRate);
  },

  cancel() {
    sessionGen += 1;
    finishPlayback?.();
    userPaused = false;
    stopSynthWatchdog();
    dropPrefetch();
    clearPauseGate();
    queue.clear();
    utterQueue = [];
    utterOffsets = [];
    utteranceQueue = [];
    utteranceDraining = false;
    currentSpeaker = "";
    activeSourceText = "";
    activeSpokenText = "";
    highlightIndex = 0;
    loadingVoice = false;
    loadingMessage = "";
    playbackError = "";
    playbackErrorCode = "";
    if (typeof speechSynthesis !== "undefined") {
      try {
        speechSynthesis.cancel();
      } catch {
        /* ignore */
      }
      // Clear Chromium's sticky paused latch so the next speak() actually audibly starts.
      clearSynthPausedLatch();
    }
    if (audioEl) {
      try {
        audioEl.pause();
        audioEl.src = "";
      } catch {
        /* ignore */
      }
      audioEl = null;
    }
    playing = false;
    emit("idle");
    emitUtteranceInfo();
  },

  replayLast(voiceId?: string, rate?: number) {
    if (!lastSpokenText && !activeSourceText) return;
    this.speak(activeSourceText || lastSpokenText, voiceId ?? currentVoiceId, rate ?? currentRate);
  },
};

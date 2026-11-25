import { confirmFromHost } from "../contexts/ConfirmModalContext";
import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { getApi } from "../hooks/usePanelApi";
import { requestMicAccess } from "./micPermission";
import {
  blobToBase64, concatFloat32, encodeWavPcm16, isTooShortRecording,
  resampleLinear, startPcmCapture, type PcmCapture,
} from "./pcmCapture";
import { toSpeechError } from "./speechErrors";
import type { TranscriptionHandlers, TranscriptionSession } from "./transcriptionSession";

const RATE = 16_000;

export function createLocalDictationSession(): TranscriptionSession {
  let closed = false;
  let recording = false;
  let capture: PcmCapture | null = null;
  let handlers: TranscriptionHandlers = {};
  let chunks: Float32Array[] = [];
  let length = 0;
  let quiet = 0;
  let pending = Promise.resolve();

  function release() {
    closed = true;
    recording = false;
    chunks = [];
    const running = capture;
    capture = null;
    return running?.stop();
  }

  function flushPhrase() {
    const phrase = concatFloat32(chunks);
    chunks = [];
    length = 0;
    quiet = 0;
    if (isTooShortRecording(phrase, RATE)) return;
    pending = pending.then(async () => {
      if (closed) return;
      // Whisper needs at least one second even for a single short word.
      const padded = phrase.length < RATE ? new Float32Array(RATE) : phrase;
      if (padded !== phrase) padded.set(phrase);
      const audio = await blobToBase64(encodeWavPcm16(padded, RATE));
      if (closed) return;
      const result = await runBridgeJob<{ ok: boolean; text?: string; error?: string }>(
        "voice_whisper_transcribe", [audio], 100_000,
      );
      if (closed) return;
      if (!result?.ok) throw new Error(result?.error || "Local transcription failed.");
      if (result.text?.trim()) handlers.onFinal?.(result.text.trim());
    }).catch((error) => {
      if (closed) return;
      void release();
      handlers.onError?.(toSpeechError(error));
      handlers.onStateChange?.("error");
    });
  }

  function onFrame(input: Float32Array, sampleRate: number) {
    if (!recording) return;
    const audible = input.some((value) => Math.abs(value) >= 0.008);
    if (!audible && !length) return;
    const frame = resampleLinear(input, sampleRate, RATE);
    chunks.push(frame === input ? frame.slice() : frame);
    length += frame.length;
    quiet = audible ? 0 : quiet + frame.length;
    // At 20 seconds the base64 PCM stays below the native bridge’s 1 MiB message limit.
    if (quiet >= RATE * 0.8 || length >= RATE * 20) flushPhrase();
  }

  return {
    kind: "batch",
    async start(next = {}) {
      handlers = next;
      handlers.onStateChange?.("connecting");
      const installed = await getApi()?.voice_whisper_installed?.();
      if (closed) return;
      if (installed !== true) {
        const confirmed = await confirmFromHost({
          title: "Install offline dictation?",
          message: "Download Whisper Small Q8 and its speech engine (about 273 MB). Dictate in 99 languages on this PC, even offline.",
          confirmLabel: "Install and dictate",
          cancelLabel: "Later",
        });
        if (closed) return;
        if (confirmed !== true) {
          handlers.onStateChange?.("idle");
          return;
        }
        handlers.onStateChange?.("installing");
        const result = await runBridgeJob<{ ok: boolean; error?: string }>("voice_whisper_install", [], 600_000);
        if (closed) return;
        if (!result?.ok) throw new Error(result?.error || "Installation failed. Click Dictate to retry.");
        handlers.onStateChange?.("connecting");
      }
      const stream = await requestMicAccess();
      if (closed) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      try {
        capture = await startPcmCapture(stream, onFrame);
      } catch (error) {
        stream.getTracks().forEach((track) => track.stop());
        throw error;
      }
      if (closed) {
        await release();
        return;
      }
      recording = true;
      handlers.onStateChange?.("listening");
    },
    async stop() {
      if (!recording) {
        this.abort();
        return;
      }
      recording = false;
      handlers.onStateChange?.("transcribing");
      flushPhrase();
      const running = capture;
      capture = null;
      await running?.stop();
      await pending;
      if (!closed) handlers.onStateChange?.("idle");
    },
    abort() {
      void release();
      handlers.onStateChange?.("idle");
    },
  };
}

import { useCallback, useRef, useState } from "react";

import {
  createBatchTranscriptionSession,
  type TranscriptionSession,
  type TranscriptionState,
} from "./transcriptionSession";

export type DictationStatus = TranscriptionState;

/**
 * Push-to-talk dictation for the chat composer (batch Whisper).
 * Stop → text in the box. Never sends — the user presses Send.
 */
export function useDictation(opts: {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}) {
  const sessionRef = useRef<TranscriptionSession | null>(null);
  const [status, setStatus] = useState<DictationStatus>("idle");
  const [error, setError] = useState("");

  const start = useCallback(async () => {
    if (opts.disabled) return;
    setError("");
    sessionRef.current?.abort();
    const session = createBatchTranscriptionSession();
    sessionRef.current = session;
    try {
      await session.start({
        onStateChange: setStatus,
        onFinal: (text) => {
          const t = text.trim();
          if (t) opts.onTranscript(t);
        },
        onError: (msg) => setError(msg),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
    }
  }, [opts]);

  const stop = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    await session.stop();
  }, []);

  const toggle = useCallback(async () => {
    if (status === "listening") {
      await stop();
      return;
    }
    if (status === "transcribing") return;
    await start();
  }, [start, stop, status]);

  const abort = useCallback(() => {
    sessionRef.current?.abort();
    sessionRef.current = null;
    setStatus("idle");
  }, []);

  return { status, error, start, stop, toggle, abort, isRecording: status === "listening" };
}

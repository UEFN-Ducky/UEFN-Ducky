import { useCallback, useEffect, useRef, useState } from "react";

import { toSpeechError, type SpeechErrorAction } from "./speechErrors";
import {
  createBatchTranscriptionSession,
  type TranscriptionSession,
  type TranscriptionState,
} from "./transcriptionSession";

export type DictationStatus = TranscriptionState;

export type VoiceNotice = {
  kind: "error" | "info";
  message: string;
  action?: SpeechErrorAction;
};

const ERROR_MS = 9000;
const INFO_MS = 5000;

/** A toast that clears itself; errors stay a little longer than tips. */
export function useVoiceNotice() {
  const [notice, setNotice] = useState<VoiceNotice | null>(null);
  const timerRef = useRef<number | null>(null);

  const clear = useCallback(() => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    setNotice(null);
  }, []);

  const show = useCallback((next: VoiceNotice) => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    setNotice(next);
    timerRef.current = window.setTimeout(
      () => {
        timerRef.current = null;
        setNotice(null);
      },
      next.kind === "error" ? ERROR_MS : INFO_MS,
    );
  }, []);

  useEffect(() => clear, [clear]);

  return { notice, show, clear };
}

/**
 * Dictation for the chat composer: words stream into the box while you talk
 * (interim, then confirmed), Stop leaves them there. Never sends — you press Send.
 */
export function useDictation(opts: {
  /** Unconfirmed words of the phrase being spoken; "" clears them. */
  onInterim: (text: string) => void;
  /** Confirmed words — append to the draft. */
  onFinal: (text: string) => void;
  /** Recording began (true) / fully ended (false). */
  onSessionChange?: (active: boolean) => void;
  disabled?: boolean;
}) {
  const sessionRef = useRef<TranscriptionSession | null>(null);
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const activeRef = useRef(false);
  const [status, setStatus] = useState<DictationStatus>("idle");
  const { notice, show, clear } = useVoiceNotice();

  const setActive = useCallback((on: boolean) => {
    if (activeRef.current === on) return;
    activeRef.current = on;
    if (!on) optsRef.current.onInterim("");
    optsRef.current.onSessionChange?.(on);
  }, []);

  const start = useCallback(async () => {
    if (optsRef.current.disabled) return;
    clear();
    sessionRef.current?.abort();
    const session = createBatchTranscriptionSession();
    sessionRef.current = session;
    setStatus("connecting");
    setActive(true);
    try {
      await session.start({
        onStateChange: (s) => {
          if (sessionRef.current !== session) return;
          setStatus(s);
          if (s === "idle" || s === "error") setActive(false);
        },
        onInterim: (text) => {
          if (sessionRef.current === session) optsRef.current.onInterim(text);
        },
        onFinal: (text) => {
          const t = text.trim();
          if (t && sessionRef.current === session) optsRef.current.onFinal(t);
        },
        onError: (err) => {
          if (sessionRef.current !== session) return;
          show({ kind: "error", message: err.message, action: err.action });
          setActive(false);
        },
        onNotice: (message) => show({ kind: "info", message }),
      });
    } catch (err) {
      if (sessionRef.current !== session) return;
      const e = toSpeechError(err);
      show({ kind: "error", message: e.message, action: e.action });
      sessionRef.current = null;
      setStatus("idle");
      setActive(false);
    }
  }, [clear, setActive, show]);

  const stop = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    await session.stop();
    if (sessionRef.current === session) sessionRef.current = null;
    setStatus("idle");
    setActive(false);
  }, [setActive]);

  const abort = useCallback(() => {
    sessionRef.current?.abort();
    sessionRef.current = null;
    setStatus("idle");
    setActive(false);
  }, [setActive]);

  const toggle = useCallback(async () => {
    if (status === "connecting") {
      abort();
      return;
    }
    if (status === "listening") {
      await stop();
      return;
    }
    if (status === "transcribing" || status === "installing") return;
    await start();
  }, [abort, start, stop, status]);

  useEffect(
    () => () => {
      sessionRef.current?.abort();
      sessionRef.current = null;
    },
    [],
  );

  useEffect(() => {
    if (opts.disabled && sessionRef.current) abort();
  }, [opts.disabled, abort]);

  return {
    status,
    notice,
    dismissNotice: clear,
    start,
    stop,
    toggle,
    abort,
    isRecording: status === "listening",
    isBusy: status === "connecting" || status === "installing" || status === "transcribing",
  };
}

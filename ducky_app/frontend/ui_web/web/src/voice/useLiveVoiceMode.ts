import { useCallback, useEffect, useRef, useState } from "react";

import { getLiveVoiceState, patchLiveVoiceState } from "./liveChats";
import {
  claimMic,
  interruptLiveSpeak,
  isLiveChat,
  releaseMic,
  updateLiveChatVoice,
} from "./liveSpeakService";
import {
  getCurrentSpokenLine,
  getLiveSpeakTransport,
  liveSpeakQueueLength,
  speakNewest,
  speakNext,
  speakPrev,
  subscribeLiveSpeakTransport,
} from "./liveSpeakQueue";
import { appendLiveUtterance } from "./liveUtterance";
import { isLikelyEcho, shouldAcceptLiveFinal, shouldReturnToListeningAfterAnswer } from "./liveTurnGates";
import { toSpeechError } from "./speechErrors";
import {
  createStreamingTranscriptionSession,
  prewarmSpeech,
  type TranscriptionSession,
} from "./transcriptionSession";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings, resolveSpeed, resolveVoiceId, subscribeVoiceSettings } from "./voiceSettings";

export type LiveVoiceStatus = "off" | "connecting" | "listening" | "thinking" | "speaking" | "error" | "muted";

/** Quiet time after the last confirmed words before a spoken turn counts as finished. */
const TURN_END_MS = 900;
/** Transient listen failures (network blips, helper restarts) retry this many times. */
const MAX_RETRIES = 3;
const RETRY_MS = 1200;

function ttsBusy(): boolean {
  return !shouldAcceptLiveFinal({ isSpeaking: ttsEngine.isSpeaking(), queueLength: liveSpeakQueueLength() });
}

function currentSpokenText(): string {
  const line = getCurrentSpokenLine();
  if (line?.resolvedText) return line.resolvedText;
  if (typeof line?.text === "string") return line.text;
  return ttsEngine.getProgress().spokenText;
}

/**
 * Mic + composer + transport for a mounted live chat.
 * Narration lives in liveSpeakService and survives tab unmount.
 */
export function useLiveVoiceMode(opts: {
  enabled: boolean;
  chatId: string;
  voiceId?: string;
  speed?: number;
  /** Mic off — type only; replies still speak. */
  muted?: boolean;
  /** Words of the phrase being spoken right now ("" clears). */
  onInterim: (text: string) => void;
  /** Confirmed words of the user's turn. */
  onTranscript: (text: string) => void;
  /** The user stopped talking for a beat — their turn is complete. */
  onTurnEnd?: () => void;
  agentRunning?: boolean;
  isGroup?: boolean;
}) {
  const sessionRef = useRef<TranscriptionSession | null>(null);
  const [status, setStatus] = useState<LiveVoiceStatus>(() =>
    isLiveChat(opts.chatId) ? getLiveVoiceState(opts.chatId).status : "off",
  );
  const [error, setError] = useState("");
  const [pendingText, setPendingText] = useState("");
  const [transport, setTransport] = useState(() => getLiveSpeakTransport());
  const [sttProvider, setSttProvider] = useState(() => getVoiceSettings().sttProvider);
  const turnTimerRef = useRef<number | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const retriesRef = useRef(0);
  const micReadyRef = useRef(false);
  const onInterimRef = useRef(opts.onInterim);
  const onTranscriptRef = useRef(opts.onTranscript);
  const onTurnEndRef = useRef(opts.onTurnEnd);
  const voiceIdRef = useRef(opts.voiceId);
  const speedRef = useRef(opts.speed);
  const chatIdRef = useRef(opts.chatId);
  const mutedRef = useRef(Boolean(opts.muted));
  const pendingTextRef = useRef("");
  onInterimRef.current = opts.onInterim;
  onTranscriptRef.current = opts.onTranscript;
  onTurnEndRef.current = opts.onTurnEnd;
  voiceIdRef.current = opts.voiceId;
  speedRef.current = opts.speed;
  chatIdRef.current = opts.chatId;
  mutedRef.current = Boolean(opts.muted);

  const publish = useCallback((patch: Parameters<typeof patchLiveVoiceState>[1]) => {
    patchLiveVoiceState(chatIdRef.current, patch);
  }, []);

  const setPending = useCallback((text: string) => {
    pendingTextRef.current = text;
    setPendingText(text);
  }, []);

  const clearTurnTimer = () => {
    if (turnTimerRef.current != null) {
      window.clearTimeout(turnTimerRef.current);
      turnTimerRef.current = null;
    }
  };

  const clearRetryTimer = () => {
    if (retryTimerRef.current != null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  };

  /** Clear the ref first: abort() reports "idle", which must not read as a crash. */
  const dropSession = () => {
    const running = sessionRef.current;
    sessionRef.current = null;
    micReadyRef.current = false;
    running?.abort();
  };

  const stopMic = useCallback(() => {
    clearTurnTimer();
    clearRetryTimer();
    dropSession();
    releaseMic(chatIdRef.current);
    onInterimRef.current("");
    setPending("");
  }, [setPending]);

  const bargeIn = useCallback(() => {
    interruptLiveSpeak();
    publish({ status: "listening", speakerName: "", nextSpeaker: "" });
    setStatus("listening");
  }, [publish]);

  const startMicRef = useRef<() => Promise<void>>(async () => undefined);

  const startMic = useCallback(async () => {
    if (mutedRef.current) return;
    if (!claimMic(chatIdRef.current)) return;
    clearRetryTimer();
    setError("");
    setPending("");
    publish({ error: "", errorAction: undefined, userInterim: "" });
    dropSession();
    const session = createStreamingTranscriptionSession();
    sessionRef.current = session;
    const voice = resolveVoiceId(voiceIdRef.current);
    ttsEngine.setVoice(voice || getVoiceSettings().defaultVoice);
    ttsEngine.setRate(resolveSpeed(speedRef.current));
    const mine = () => sessionRef.current === session;

    const fail = (message: string, action?: Parameters<typeof publish>[0]["errorAction"]) => {
      if (!mine()) return;
      dropSession();
      releaseMic(chatIdRef.current);
      onInterimRef.current("");
      // Fixable problems wait for the user; blips retry on their own.
      if (!action && retriesRef.current < MAX_RETRIES) {
        retriesRef.current += 1;
        setStatus("connecting");
        publish({ status: "connecting", error: "", notice: "Reconnecting the mic…" });
        retryTimerRef.current = window.setTimeout(() => {
          retryTimerRef.current = null;
          void startMicRef.current();
        }, RETRY_MS * retriesRef.current);
        return;
      }
      setError(message);
      setStatus("error");
      publish({ status: "error", error: message, errorAction: action, userInterim: "", notice: "" });
    };

    setStatus("connecting");
    publish({ status: "connecting" });
    try {
      await session.start({
        onInterim: (text) => {
          if (!mine()) return;
          if (ttsBusy()) {
            // Ducky's own voice leaks into the mic; only real new words interrupt it.
            if (text && !isLikelyEcho(text, currentSpokenText())) bargeIn();
            else return;
          }
          if (text) clearTurnTimer();
          onInterimRef.current(text);
          publish({ userInterim: text, status: "listening" });
        },
        onFinal: (text) => {
          if (!mine()) return;
          onInterimRef.current("");
          const trimmed = text.trim();
          if (!trimmed) {
            publish({ userInterim: "" });
            return;
          }
          if (ttsBusy()) {
            if (isLikelyEcho(trimmed, currentSpokenText())) {
              publish({ userInterim: "" });
              return;
            }
            bargeIn();
          }
          retriesRef.current = 0;
          const next = appendLiveUtterance(pendingTextRef.current, trimmed);
          setPending(next);
          publish({ userInterim: "", lastUserText: next, status: "listening", notice: "" });
          setStatus("listening");
          onTranscriptRef.current(trimmed);
          clearTurnTimer();
          turnTimerRef.current = window.setTimeout(() => {
            turnTimerRef.current = null;
            if (!mine()) return;
            setPending("");
            onTurnEndRef.current?.();
          }, TURN_END_MS);
        },
        onSpeechStarted: () => {
          if (mine()) clearTurnTimer();
        },
        onError: (err) => fail(err.message, err.action),
        onNotice: (message) => {
          if (mine()) publish({ notice: message });
        },
        onStateChange: (s) => {
          if (!mine()) return;
          if (s === "listening") {
            if (mutedRef.current) return;
            micReadyRef.current = true;
            setStatus("listening");
            publish({ status: "listening", muted: false, error: "", errorAction: undefined, notice: "" });
          }
          if (s === "connecting") {
            setStatus("connecting");
            publish({ status: "connecting" });
          }
          if (s === "idle" && !mutedRef.current) {
            // The engine ended on its own (service hiccup) — reopen it.
            fail("The mic stopped listening.");
          }
        },
      });
      if (!mine()) return;
      if (mutedRef.current) {
        dropSession();
        releaseMic(chatIdRef.current);
        setStatus("muted");
        publish({ notice: "", status: "muted", muted: true });
        return;
      }
      micReadyRef.current = true;
      setStatus("listening");
      publish({ status: "listening", muted: false, error: "", errorAction: undefined, notice: "" });
    } catch (err) {
      const e = toSpeechError(err);
      fail(e.message, e.action);
    }
  }, [bargeIn, publish, setPending]);
  startMicRef.current = startMic;

  useEffect(() => subscribeVoiceSettings(() => setSttProvider(getVoiceSettings().sttProvider)), []);

  useEffect(() => {
    if (!opts.enabled) return;
    const voice = resolveVoiceId(opts.voiceId);
    ttsEngine.setVoice(voice || getVoiceSettings().defaultVoice);
    ttsEngine.setRate(resolveSpeed(opts.speed));
    updateLiveChatVoice(opts.chatId, voice || getVoiceSettings().defaultVoice, resolveSpeed(opts.speed));
  }, [opts.enabled, opts.voiceId, opts.speed, opts.chatId]);

  useEffect(() => {
    if (!opts.enabled) {
      stopMic();
      setStatus(isLiveChat(opts.chatId) ? getLiveVoiceState(opts.chatId).status : "off");
      return;
    }
    if (opts.muted) {
      stopMic();
      setStatus("muted");
      publish({ notice: "", status: "muted", muted: true, userInterim: "", error: "", errorAction: undefined });
      return;
    }
    retriesRef.current = 0;
    prewarmSpeech();
    void startMic();
    return () => {
      stopMic();
    };
  }, [opts.enabled, opts.muted, opts.chatId, sttProvider, startMic, stopMic, publish]);

  useEffect(() => {
    if (!opts.enabled) return;
    if (opts.agentRunning && !ttsEngine.isSpeaking() && micReadyRef.current) {
      setStatus("thinking");
      publish({ status: "thinking", error: "" });
      setError("");
    }
  }, [opts.enabled, opts.agentRunning, publish]);

  useEffect(() => {
    return ttsEngine.onStateChange((s) => {
      if (!opts.enabled) return;
      if (!micReadyRef.current && !mutedRef.current) return;
      if (s === "speaking") {
        setStatus("speaking");
        return;
      }
      if (s === "idle" && opts.agentRunning && micReadyRef.current && liveSpeakQueueLength() === 0) {
        setStatus("thinking");
        return;
      }
      if (s === "idle" && !opts.agentRunning) {
        if (mutedRef.current) {
          setStatus("muted");
          publish({ notice: "", status: "muted", muted: true });
          return;
        }
        if (
          micReadyRef.current && shouldReturnToListeningAfterAnswer({
            speakingAfterAnswer: true,
            moreUtterancesQueued: liveSpeakQueueLength() > 0,
          })
        ) {
          setStatus("listening");
        }
      }
    });
  }, [opts.enabled, opts.agentRunning, publish]);

  useEffect(() => {
    return subscribeLiveSpeakTransport(() => setTransport(getLiveSpeakTransport()));
  }, []);

  /** Retry after a fixable error (e.g. after turning Windows speech on). */
  const retry = useCallback(() => {
    retriesRef.current = 0;
    stopMic();
    void startMic();
  }, [startMic, stopMic]);

  const interrupt = useCallback(() => {
    interruptLiveSpeak();
    if (mutedRef.current) {
      setStatus("muted");
      publish({ status: "muted", speakerName: "", nextSpeaker: "" });
      return;
    }
    setStatus("listening");
    publish({ status: "listening", speakerName: "", nextSpeaker: "" });
  }, [publish]);

  const skip = useCallback(() => {
    speakNext();
    setStatus("speaking");
    publish({ status: "speaking" });
  }, [publish]);

  const back = useCallback(() => {
    speakPrev();
    setStatus("speaking");
    publish({ status: "speaking" });
  }, [publish]);

  const newest = useCallback(() => {
    speakNewest();
    setStatus("speaking");
    publish({ status: "speaking" });
  }, [publish]);

  return {
    status,
    error,
    pendingText,
    interrupt,
    retry,
    skip,
    back,
    newest,
    stopSession: stopMic,
    hasPrev: transport.hasPrev,
    hasNext: transport.hasNext,
    hasNewer: transport.hasNewer,
  };
}

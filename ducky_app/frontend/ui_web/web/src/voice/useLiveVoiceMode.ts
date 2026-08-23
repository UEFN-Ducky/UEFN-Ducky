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
  getLiveSpeakTransport,
  liveSpeakQueueLength,
  speakNewest,
  speakNext,
  speakPrev,
  subscribeLiveSpeakTransport,
} from "./liveSpeakQueue";
import { appendLiveUtterance } from "./liveUtterance";
import { shouldAcceptLiveFinal, shouldReturnToListeningAfterAnswer } from "./liveTurnGates";
import {
  createStreamingTranscriptionSession,
  type TranscriptionSession,
} from "./transcriptionSession";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings, resolveSpeed, resolveVoiceId } from "./voiceSettings";

export type LiveVoiceStatus = "off" | "listening" | "thinking" | "speaking" | "error" | "muted";

/**
 * Mic + composer + transport for a mounted live chat.
 * Narration lives in liveSpeakService and survives tab unmount.
 */
export function useLiveVoiceMode(opts: {
  enabled: boolean;
  chatId: string;
  voiceId?: string;
  speed?: number;
  manualSend?: boolean;
  /** Mic off — type only; replies still speak. */
  muted?: boolean;
  onInterim: (text: string) => void;
  /** Final speech → composer only. Never send. */
  onTranscript: (text: string) => void;
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
  const bargeTimerRef = useRef<number | null>(null);
  const onInterimRef = useRef(opts.onInterim);
  const onTranscriptRef = useRef(opts.onTranscript);
  const voiceIdRef = useRef(opts.voiceId);
  const speedRef = useRef(opts.speed);
  const chatIdRef = useRef(opts.chatId);
  const enabledRef = useRef(opts.enabled);
  const manualSendRef = useRef(Boolean(opts.manualSend));
  const mutedRef = useRef(Boolean(opts.muted));
  const pendingTextRef = useRef("");
  onInterimRef.current = opts.onInterim;
  onTranscriptRef.current = opts.onTranscript;
  voiceIdRef.current = opts.voiceId;
  speedRef.current = opts.speed;
  chatIdRef.current = opts.chatId;
  enabledRef.current = opts.enabled;
  manualSendRef.current = Boolean(opts.manualSend);
  mutedRef.current = Boolean(opts.muted);

  const publish = useCallback((patch: Parameters<typeof patchLiveVoiceState>[1]) => {
    patchLiveVoiceState(chatIdRef.current, patch);
  }, []);

  const setPending = useCallback((text: string) => {
    pendingTextRef.current = text;
    setPendingText(text);
  }, []);

  const clearBargeTimer = () => {
    if (bargeTimerRef.current != null) {
      window.clearTimeout(bargeTimerRef.current);
      bargeTimerRef.current = null;
    }
  };

  const stopMic = useCallback(() => {
    clearBargeTimer();
    sessionRef.current?.abort();
    sessionRef.current = null;
    releaseMic(chatIdRef.current);
    setPending("");
  }, [setPending]);

  const startMic = useCallback(async () => {
    if (mutedRef.current) return;
    if (!claimMic(chatIdRef.current)) return;
    setError("");
    setPending("");
    publish({ error: "", userInterim: "" });
    sessionRef.current?.abort();
    const session = createStreamingTranscriptionSession();
    sessionRef.current = session;
    const voice = resolveVoiceId(voiceIdRef.current);
    ttsEngine.setVoice(voice || getVoiceSettings().defaultVoice);
    ttsEngine.setRate(resolveSpeed(speedRef.current));

    try {
      await session.start({
        onInterim: (text) => {
          if (
            !shouldAcceptLiveFinal({
              isSpeaking: ttsEngine.isSpeaking(),
              queueLength: liveSpeakQueueLength(),
            })
          ) {
            return;
          }
          onInterimRef.current(text);
          publish({ userInterim: text, status: "listening" });
        },
        onFinal: (text) => {
          onInterimRef.current("");
          const trimmed = text.trim();
          if (!trimmed) {
            publish({ userInterim: "" });
            return;
          }
          if (
            !shouldAcceptLiveFinal({
              isSpeaking: ttsEngine.isSpeaking(),
              queueLength: liveSpeakQueueLength(),
            })
          ) {
            return;
          }
          const next = appendLiveUtterance(pendingTextRef.current, trimmed);
          setPending(next);
          publish({ userInterim: "", lastUserText: next, status: "listening" });
          setStatus("listening");
          onTranscriptRef.current(trimmed);
        },
        onSpeechStarted: () => {
          clearBargeTimer();
          if (!ttsEngine.isSpeaking() && ttsEngine.getUtteranceQueueLength() === 0 && liveSpeakQueueLength() === 0) {
            return;
          }
          bargeTimerRef.current = window.setTimeout(() => {
            if (ttsEngine.isSpeaking() || liveSpeakQueueLength() > 0) {
              interruptLiveSpeak();
              publish({ status: "listening", speakerName: "", nextSpeaker: "" });
              setStatus("listening");
            }
            bargeTimerRef.current = null;
          }, 350);
        },
        onSpeechStopped: () => {
          clearBargeTimer();
        },
        onError: (msg) => {
          setError(msg);
          setStatus("error");
          publish({ status: "error", error: msg });
        },
        onStateChange: (s) => {
          if (s === "listening") {
            if (mutedRef.current) return;
            setStatus("listening");
            publish({ status: "listening", muted: false });
          }
          if (s === "error") setStatus("error");
        },
      });
      if (mutedRef.current) {
        session.abort();
        sessionRef.current = null;
        releaseMic(chatIdRef.current);
        setStatus("muted");
        publish({ status: "muted", muted: true });
        return;
      }
      setStatus("listening");
      publish({ status: "listening", muted: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setStatus("error");
      publish({ status: "error", error: msg });
      releaseMic(chatIdRef.current);
    }
  }, [publish, setPending]);

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
      publish({ status: "muted", muted: true, userInterim: "", error: "" });
      return;
    }
    void startMic();
    return () => {
      stopMic();
    };
  }, [opts.enabled, opts.muted, opts.chatId, startMic, stopMic, publish]);

  useEffect(() => {
    if (!opts.enabled) return;
    if (opts.agentRunning && !ttsEngine.isSpeaking()) {
      setStatus("thinking");
      publish({ status: "thinking", error: "" });
      setError("");
    }
  }, [opts.enabled, opts.agentRunning, publish]);

  useEffect(() => {
    return ttsEngine.onStateChange((s) => {
      if (!opts.enabled) return;
      if (s === "speaking") {
        setStatus("speaking");
        return;
      }
      if (s === "idle" && opts.agentRunning && liveSpeakQueueLength() === 0) {
        setStatus("thinking");
        return;
      }
      if (s === "idle" && !opts.agentRunning) {
        if (mutedRef.current) {
          setStatus("muted");
          publish({ status: "muted", muted: true });
          return;
        }
        if (
          shouldReturnToListeningAfterAnswer({
            speakingAfterAnswer: true,
            moreUtterancesQueued: liveSpeakQueueLength() > 0,
          })
        ) {
          setStatus("listening");
        }
      }
    });
  }, [opts.enabled, opts.agentRunning]);

  useEffect(() => {
    return subscribeLiveSpeakTransport(() => setTransport(getLiveSpeakTransport()));
  }, []);

  const interrupt = useCallback(() => {
    clearBargeTimer();
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

  const replay = back;

  const sendNow = useCallback(() => {
    // Speech is already in the composer. User presses Send themselves.
    return false;
  }, []);

  return {
    status,
    error,
    pendingText,
    canSend: Boolean(pendingText.trim()),
    interrupt,
    skip,
    back,
    newest,
    replay,
    sendNow,
    stopSession: stopMic,
    hasPrev: transport.hasPrev,
    hasNext: transport.hasNext,
    hasNewer: transport.hasNewer,
  };
}

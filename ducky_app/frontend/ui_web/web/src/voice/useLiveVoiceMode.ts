import { useCallback, useEffect, useRef, useState } from "react";

import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent, MessageAuthorDto } from "../types/panel";
import { patchLiveVoiceState, setLiveVoiceChat } from "./liveChats";
import { appendLiveUtterance } from "./liveUtterance";
import {
  clearLiveSpeakQueue,
  enqueueFinalSpeak,
  enqueueProcessSpeak,
} from "./liveSpeakQueue";
import {
  shouldNarrateTool,
  speakableThinkingLine,
  speakableToolLine,
} from "./processNarration";
import { loadLastAssistantText, prepareSpokenText, speakReply } from "./speakReply";
import {
  createStreamingTranscriptionSession,
  type TranscriptionSession,
} from "./transcriptionSession";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings, resolveSpeed, resolveVoiceId } from "./voiceSettings";

export type LiveVoiceStatus = "off" | "listening" | "thinking" | "speaking" | "error";

/**
 * Hands-free loop: streaming STT + VAD barge-in + auto-send + conversational speak on assistant_done.
 * Live mode implies voice for that chat even when global spoken replies are off.
 * Group chats enqueue each ducky's reply with its own voice.
 * When manualSend is on, finals accumulate until sendNow() — pauses do not submit.
 */
export function useLiveVoiceMode(opts: {
  enabled: boolean;
  chatId: string;
  voiceId?: string;
  speed?: number;
  /** Accumulate speech until sendNow(); default auto-sends on each VAD final. */
  manualSend?: boolean;
  onInterim: (text: string) => void;
  onAutoSend: (text: string) => void;
  agentRunning?: boolean;
  isGroup?: boolean;
}) {
  const sessionRef = useRef<TranscriptionSession | null>(null);
  const [status, setStatus] = useState<LiveVoiceStatus>("off");
  const [error, setError] = useState("");
  const [pendingText, setPendingText] = useState("");
  const bargeTimerRef = useRef<number | null>(null);
  const onInterimRef = useRef(opts.onInterim);
  const onAutoSendRef = useRef(opts.onAutoSend);
  const voiceIdRef = useRef(opts.voiceId);
  const speedRef = useRef(opts.speed);
  const chatIdRef = useRef(opts.chatId);
  const speakingAfterDoneRef = useRef(false);
  const enabledRef = useRef(opts.enabled);
  const manualSendRef = useRef(Boolean(opts.manualSend));
  const isGroupRef = useRef(Boolean(opts.isGroup));
  const pendingGroupSpeaksRef = useRef(0);
  const pendingTextRef = useRef("");
  /** Last time we spoke a thinking line (throttle). */
  const lastThinkingNarrationAtRef = useRef(0);
  /** Thinking text buffer for one burst → one spoken line. */
  const thinkingBurstRef = useRef("");
  const thinkingNarrateTimerRef = useRef<number | null>(null);
  onInterimRef.current = opts.onInterim;
  onAutoSendRef.current = opts.onAutoSend;
  voiceIdRef.current = opts.voiceId;
  speedRef.current = opts.speed;
  chatIdRef.current = opts.chatId;
  enabledRef.current = opts.enabled;
  manualSendRef.current = Boolean(opts.manualSend);
  isGroupRef.current = Boolean(opts.isGroup);

  const clearThinkingNarrateTimer = () => {
    if (thinkingNarrateTimerRef.current != null) {
      window.clearTimeout(thinkingNarrateTimerRef.current);
      thinkingNarrateTimerRef.current = null;
    }
  };

  const speakProcessLine = useCallback((line: string) => {
    const cleaned = (line || "").trim();
    if (!cleaned) return;
    const voice = resolveVoiceId(voiceIdRef.current) || getVoiceSettings().defaultVoice;
    enqueueProcessSpeak(cleaned, voice, resolveSpeed(speedRef.current));
  }, []);

  const clearBargeTimer = () => {
    if (bargeTimerRef.current != null) {
      window.clearTimeout(bargeTimerRef.current);
      bargeTimerRef.current = null;
    }
  };

  const publish = useCallback((patch: Parameters<typeof patchLiveVoiceState>[1]) => {
    patchLiveVoiceState(chatIdRef.current, patch);
  }, []);

  const setPending = useCallback((text: string) => {
    pendingTextRef.current = text;
    setPendingText(text);
  }, []);

  const stopSession = useCallback(async () => {
    clearBargeTimer();
    clearThinkingNarrateTimer();
    thinkingBurstRef.current = "";
    clearLiveSpeakQueue();
    speakingAfterDoneRef.current = false;
    pendingGroupSpeaksRef.current = 0;
    sessionRef.current?.abort();
    sessionRef.current = null;
    setPending("");
    setStatus("off");
    setError("");
    publish({ status: "off", userInterim: "", error: "", speakerName: "", nextSpeaker: "" });
  }, [publish, setPending]);

  const startSession = useCallback(async () => {
    setError("");
    setPending("");
    publish({ error: "", spokenText: "", speakerName: "", nextSpeaker: "" });
    sessionRef.current?.abort();
    const session = createStreamingTranscriptionSession();
    sessionRef.current = session;
    const voice = resolveVoiceId(voiceIdRef.current);
    ttsEngine.setVoice(voice || getVoiceSettings().defaultVoice);
    ttsEngine.setRate(resolveSpeed(speedRef.current));

    try {
      await session.start({
        onInterim: (text) => {
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
          if (manualSendRef.current) {
            const next = appendLiveUtterance(pendingTextRef.current, trimmed);
            setPending(next);
            publish({ userInterim: "", lastUserText: next, status: "listening" });
            setStatus("listening");
            return;
          }
          publish({ userInterim: "", lastUserText: trimmed, status: "thinking" });
          setStatus("thinking");
          onAutoSendRef.current(trimmed);
        },
        onSpeechStarted: () => {
          // ponytail: require ~350ms of sustained speech before barge-in while TTS plays.
          clearBargeTimer();
          if (!ttsEngine.isSpeaking() && ttsEngine.getUtteranceQueueLength() === 0) return;
          bargeTimerRef.current = window.setTimeout(() => {
            if (ttsEngine.isSpeaking() || ttsEngine.getUtteranceQueueLength() > 0) {
              clearLiveSpeakQueue();
              speakingAfterDoneRef.current = false;
              pendingGroupSpeaksRef.current = 0;
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
          if (s === "listening" && !speakingAfterDoneRef.current) {
            setStatus("listening");
            publish({ status: "listening" });
          }
          if (s === "error") setStatus("error");
        },
      });
      setStatus("listening");
      publish({ status: "listening" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setStatus("error");
      publish({ status: "error", error: msg });
    }
  }, [publish, setPending]);

  // Live voice/speed pickers — apply without restarting the mic session.
  useEffect(() => {
    if (!opts.enabled) return;
    const voice = resolveVoiceId(opts.voiceId);
    ttsEngine.setVoice(voice || getVoiceSettings().defaultVoice);
    ttsEngine.setRate(resolveSpeed(opts.speed));
  }, [opts.enabled, opts.voiceId, opts.speed]);

  useEffect(() => {
    setLiveVoiceChat(opts.chatId, opts.enabled);
    if (!opts.enabled) {
      void stopSession();
      return;
    }
    void startSession();
    return () => {
      void stopSession();
      setLiveVoiceChat(opts.chatId, false);
    };
  }, [opts.enabled, opts.chatId, startSession, stopSession]);

  // Agent running → thinking. Do not clear speakingAfterDoneRef here — that
  // flag is owned by the assistant_done → speak path and must survive until TTS ends.
  useEffect(() => {
    if (!opts.enabled) return;
    if (opts.agentRunning && !speakingAfterDoneRef.current) {
      setStatus("thinking");
      publish({ status: "thinking", error: "" });
      setError("");
    }
  }, [opts.enabled, opts.agentRunning, publish]);

  // Process chatter (tools / thinking) + final reply when the turn ends.
  useEffect(() => {
    installAgentEventBus();
    return subscribeAgentEvents((event: AgentEvent) => {
      if (!enabledRef.current) return;
      const convId = event.conv_id?.trim();
      if (!convId || convId !== chatIdRef.current) return;

      if (event.type === "tool") {
        clearThinkingNarrateTimer();
        thinkingBurstRef.current = "";
        const talk = getVoiceSettings().processTalk;
        if (!shouldNarrateTool(talk)) return;
        const name = event.tool?.name || event.text || "";
        speakProcessLine(speakableToolLine(name));
        return;
      }

      if (event.type === "thinking") {
        const chunk = typeof event.text === "string" ? event.text : "";
        if (!chunk) return;
        thinkingBurstRef.current += chunk;
        if (thinkingNarrateTimerRef.current != null) return;
        thinkingNarrateTimerRef.current = window.setTimeout(() => {
          thinkingNarrateTimerRef.current = null;
          if (!enabledRef.current) return;
          const now = Date.now();
          // ponytail: one thinking line per ~8s — keep the user in the loop, not the essay.
          if (now - lastThinkingNarrationAtRef.current < 8000) {
            thinkingBurstRef.current = "";
            return;
          }
          const line = speakableThinkingLine(thinkingBurstRef.current, getVoiceSettings().processTalk);
          thinkingBurstRef.current = "";
          if (!line) return;
          lastThinkingNarrationAtRef.current = now;
          speakProcessLine(line);
        }, 450);
        return;
      }

      if (event.type !== "assistant_done") return;

      clearThinkingNarrateTimer();
      thinkingBurstRef.current = "";
      speakingAfterDoneRef.current = true;
      setStatus("thinking");
      publish({ status: "thinking" });

      const author = event.author as MessageAuthorDto | undefined;
      const eventText = typeof event.text === "string" ? event.text : "";
      const group = isGroupRef.current;
      if (group) pendingGroupSpeaksRef.current += 1;

      void (async () => {
        if (!enabledRef.current) return;
        const voice = author?.tts_voice || voiceIdRef.current;
        try {
          if (group) {
            const result = await speakReply(convId, voice, {
              text: eventText || undefined,
              enqueue: true,
              speaker: author?.name,
              speed: author?.tts_speed ?? speedRef.current,
              onSpokenText: (spoken) =>
                publish({
                  spokenText: spoken,
                  speakerName: author?.name || "",
                }),
            });
            if (!enabledRef.current) return;
            if (!result.ok) {
              pendingGroupSpeaksRef.current = Math.max(0, pendingGroupSpeaksRef.current - 1);
              setError(result.error || "Could not speak reply");
              setStatus("error");
              publish({ status: "error", error: result.error || "Could not speak reply" });
              speakingAfterDoneRef.current = false;
              return;
            }
            setStatus("speaking");
            publish({
              status: "speaking",
              spokenText: result.spoken || "",
              speakerName: author?.name || ttsEngine.getCurrentSpeaker() || "Ducky",
            });
            return;
          }

          // Solo live: FIFO queue — process lines first, then the answer.
          const text = (eventText || "").trim() || (await loadLastAssistantText(convId));
          if (!enabledRef.current) return;
          if (!text) {
            setError("empty reply");
            setStatus("error");
            publish({ status: "error", error: "empty reply" });
            speakingAfterDoneRef.current = false;
            return;
          }
          const spoken = await prepareSpokenText(text);
          if (!enabledRef.current) return;
          if (!spoken) {
            setError("empty spoken text");
            setStatus("error");
            publish({ status: "error", error: "empty spoken text" });
            speakingAfterDoneRef.current = false;
            return;
          }
          publish({ spokenText: spoken, speakerName: author?.name || "" });
          enqueueFinalSpeak(spoken, voice || getVoiceSettings().defaultVoice, resolveSpeed(author?.tts_speed ?? speedRef.current));
          setStatus("speaking");
          publish({
            status: "speaking",
            spokenText: spoken,
            speakerName: author?.name || "Ducky",
          });
        } catch (err) {
          if (group) pendingGroupSpeaksRef.current = Math.max(0, pendingGroupSpeaksRef.current - 1);
          const msg = err instanceof Error ? err.message : String(err);
          setError(msg || "Could not speak reply");
          setStatus("error");
          publish({ status: "error", error: msg || "Could not speak reply" });
          speakingAfterDoneRef.current = false;
        }
      })();
    });
  }, [publish, speakProcessLine]);

  useEffect(() => {
    return ttsEngine.onStateChange((s) => {
      if (!opts.enabled) return;
      if (s === "speaking") {
        setStatus("speaking");
        publish({
          status: "speaking",
          speakerName: ttsEngine.getCurrentSpeaker() || undefined,
        });
        return;
      }
      // Process chatter finished mid-turn → show Thinking until the next line / answer.
      if (s === "idle" && opts.agentRunning && !speakingAfterDoneRef.current) {
        setStatus("thinking");
        publish({ status: "thinking" });
        return;
      }
      // Finished speaking → back to listening (unless more group utterances queued).
      if (speakingAfterDoneRef.current) {
        const more =
          ttsEngine.getUtteranceQueueLength() > 0 || pendingGroupSpeaksRef.current > 1;
        if (isGroupRef.current) {
          pendingGroupSpeaksRef.current = Math.max(0, pendingGroupSpeaksRef.current - 1);
        }
        if (more) {
          setStatus("speaking");
          publish({ status: "speaking" });
          return;
        }
        speakingAfterDoneRef.current = false;
        pendingGroupSpeaksRef.current = 0;
        if (!opts.agentRunning) {
          setStatus("listening");
          publish({ status: "listening", speakerName: "", nextSpeaker: "" });
        }
      }
    });
  }, [opts.enabled, opts.agentRunning, publish]);

  useEffect(() => {
    return ttsEngine.onUtteranceChange((info) => {
      if (!opts.enabled) return;
      publish({
        speakerName: info.speaker || undefined,
        nextSpeaker: info.remaining > 0 ? `${info.remaining} more` : "",
      });
    });
  }, [opts.enabled, publish]);

  const interrupt = useCallback(() => {
    clearBargeTimer();
    clearLiveSpeakQueue();
    speakingAfterDoneRef.current = false;
    pendingGroupSpeaksRef.current = 0;
    setStatus("listening");
    publish({ status: "listening", speakerName: "", nextSpeaker: "" });
  }, [publish]);

  const skip = useCallback(() => {
    if (isGroupRef.current && ttsEngine.getUtteranceQueueLength() > 0) {
      ttsEngine.skipUtterance();
      setStatus("speaking");
      publish({ status: "speaking" });
      return;
    }
    clearLiveSpeakQueue();
    speakingAfterDoneRef.current = false;
    pendingGroupSpeaksRef.current = 0;
    setStatus("listening");
    publish({ status: "listening", speakerName: "", nextSpeaker: "" });
  }, [publish]);

  const replay = useCallback(() => {
    clearLiveSpeakQueue();
    const voice = resolveVoiceId(voiceIdRef.current);
    ttsEngine.replayLast(voice, resolveSpeed(speedRef.current));
    setStatus("speaking");
    publish({ status: "speaking" });
  }, [publish]);

  const sendNow = useCallback(() => {
    const text = pendingTextRef.current.trim();
    if (!text || !enabledRef.current) return false;
    setPending("");
    publish({ userInterim: "", lastUserText: text, status: "thinking" });
    setStatus("thinking");
    onAutoSendRef.current(text);
    return true;
  }, [publish, setPending]);

  return {
    status,
    error,
    pendingText,
    canSend: Boolean(pendingText.trim()),
    interrupt,
    skip,
    replay,
    sendNow,
    stopSession,
  };
}

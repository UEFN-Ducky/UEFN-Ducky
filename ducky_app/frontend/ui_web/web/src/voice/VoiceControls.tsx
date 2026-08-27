import { useCallback, useEffect, useRef, useState } from "react";

import { Icons } from "../icons/Icons";
import { startLiveChat, stopLiveChat } from "./liveSpeakService";
import { useDictation } from "./useDictation";
import { useIsLiveChat } from "./useLiveChatPresence";
import { useLiveVoiceMode } from "./useLiveVoiceMode";
import { VoiceOverlay } from "./VoiceOverlay";
import { mapReadAlong } from "./TtsReadAlong";
import { ttsEngine, type TtsProgress } from "./ttsEngine";
import {
  getVoiceSettings,
  loadVoiceSettings,
  resolveSpeed,
  resolveVoiceId,
  saveVoiceSettings,
  subscribeVoiceSettings,
} from "./voiceSettings";

export type LiveVoiceUiHandlers = {
  onClose: () => void;
  onBack: () => void;
  onForward: () => void;
  onNewest: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  hasNewer: boolean;
  onSend: () => void;
  canSend: boolean;
  manualSend: boolean;
  setManualSend: (value: boolean) => void;
  muted: boolean;
  voiceId: string;
  speed: number;
  setVoiceId: (value: string) => void;
  setSpeed: (value: number) => void;
  processTalk: number;
  setProcessTalk: (value: number) => void;
};

export type VoiceControlsProps = {
  chatId: string;
  disabled?: boolean;
  inputText: string;
  setInputText: (text: string | ((prev: string) => string)) => void;
  onSend: (text?: string) => void;
  streamText?: string;
  agentRunning?: boolean;
  /** Per-ducky voice override (tts_voice). */
  duckyVoice?: string;
  /** Per-ducky talking-speed override (tts_speed; 0 → global default). */
  duckySpeed?: number;
  /** Multi-ducky group chat — enqueue each speaker's voice. */
  isGroup?: boolean;
  /** Composer slides the live panel above the textarea; parent renders VoiceOverlay. */
  onLiveChange?: (live: boolean, handlers: LiveVoiceUiHandlers | null) => void;
};

/**
 * Mic + live-mode transport — the only ChatPane voice UI touch point.
 */
export function VoiceControls({
  chatId,
  disabled,
  inputText: _inputText,
  setInputText,
  onSend: _onSend,
  streamText,
  agentRunning,
  duckyVoice,
  duckySpeed,
  isGroup,
  onLiveChange,
}: VoiceControlsProps) {
  const live = useIsLiveChat(chatId);
  const [voiceOn, setVoiceOn] = useState(() => getVoiceSettings().enabled);
  const [manualSend, setManualSendState] = useState(() => getVoiceSettings().liveManualSend);
  const [sessionVoice, setSessionVoice] = useState(() => resolveVoiceId(duckyVoice));
  const [sessionSpeed, setSessionSpeed] = useState(() => resolveSpeed(duckySpeed));
  const [processTalk, setProcessTalkState] = useState(() => getVoiceSettings().processTalk);
  const [muted, setMuted] = useState(false);
  const onLiveChangeRef = useRef(onLiveChange);
  onLiveChangeRef.current = onLiveChange;

  useEffect(() => {
    void loadVoiceSettings().then((s) => {
      setVoiceOn(s.enabled);
      setManualSendState(s.liveManualSend);
      setProcessTalkState(s.processTalk);
      if (!live) {
        setSessionVoice(resolveVoiceId(duckyVoice));
        setSessionSpeed(resolveSpeed(duckySpeed));
      }
    });
    return subscribeVoiceSettings(() => {
      const s = getVoiceSettings();
      setVoiceOn(s.enabled);
      setManualSendState(s.liveManualSend);
      setProcessTalkState(s.processTalk);
    });
  }, [live, duckyVoice, duckySpeed]);

  useEffect(() => {
    if (live) return;
    setSessionVoice(resolveVoiceId(duckyVoice));
    setSessionSpeed(resolveSpeed(duckySpeed));
  }, [live, duckyVoice, duckySpeed]);

  const appendTranscript = useCallback(
    (text: string) => {
      setInputText((prev) => {
        const base = prev.trim();
        return base ? `${base} ${text}` : text;
      });
    },
    [setInputText],
  );

  const dictation = useDictation({
    disabled: disabled || live,
    onTranscript: appendTranscript,
  });

  const liveMode = useLiveVoiceMode({
    enabled: live,
    chatId,
    voiceId: sessionVoice,
    speed: sessionSpeed,
    manualSend,
    muted,
    agentRunning,
    isGroup,
    onInterim: () => {
      // Interim stays in the live panel — never overwrite the typed draft.
    },
    onTranscript: appendTranscript,
  });

  useEffect(() => {
    if (!live) setMuted(false);
  }, [live]);

  const { skip, back, newest, sendNow, canSend, hasPrev, hasNext, hasNewer } = liveMode;

  const exitLive = useCallback(() => {
    stopLiveChat(chatId);
  }, [chatId]);

  const setManualSend = useCallback((value: boolean) => {
    setManualSendState(value);
    void saveVoiceSettings({ liveManualSend: value });
  }, []);

  const setVoiceId = useCallback((value: string) => {
    setSessionVoice(value);
    ttsEngine.setVoice(value);
    void saveVoiceSettings({ defaultVoice: value });
  }, []);

  const setSpeed = useCallback((value: number) => {
    setSessionSpeed(value);
    ttsEngine.setRate(value);
    void saveVoiceSettings({ defaultSpeed: value });
  }, []);

  const setProcessTalk = useCallback((value: number) => {
    setProcessTalkState(value);
    void saveVoiceSettings({ processTalk: value });
  }, []);

  useEffect(() => {
    const notify = onLiveChangeRef.current;
    if (!notify) return;
    if (live) {
      notify(true, {
        onClose: exitLive,
        onBack: back,
        onForward: skip,
        onNewest: newest,
        hasPrev,
        hasNext,
        hasNewer,
        onSend: () => {
          sendNow();
        },
        canSend: canSend && !agentRunning,
        manualSend,
        setManualSend,
        muted,
        voiceId: sessionVoice,
        speed: sessionSpeed,
        setVoiceId,
        setSpeed,
        processTalk,
        setProcessTalk,
      });
    } else {
      notify(false, null);
    }
  }, [
    live,
    exitLive,
    skip,
    back,
    newest,
    hasPrev,
    hasNext,
    hasNewer,
    sendNow,
    canSend,
    agentRunning,
    manualSend,
    setManualSend,
    muted,
    sessionVoice,
    sessionSpeed,
    setVoiceId,
    setSpeed,
    processTalk,
    setProcessTalk,
  ]);

  // Speak-along outside live mode when style is speak_along (normal chat only).
  const streamLenRef = useRef(0);
  useEffect(() => {
    if (live || !voiceOn) {
      streamLenRef.current = 0;
      return;
    }
    const settings = getVoiceSettings();
    if (settings.spokenStyle !== "speak_along") {
      streamLenRef.current = 0;
      return;
    }
    const text = streamText || "";
    if (!agentRunning) {
      if (streamLenRef.current > 0) {
        ttsEngine.flush();
        streamLenRef.current = 0;
      }
      return;
    }
    if (text.length < streamLenRef.current) {
      streamLenRef.current = 0;
      ttsEngine.cancel();
    }
    const delta = text.slice(streamLenRef.current);
    streamLenRef.current = text.length;
    if (delta) {
      ttsEngine.setVoice(resolveVoiceId(duckyVoice));
      ttsEngine.setRate(resolveSpeed(duckySpeed));
      ttsEngine.enqueue(delta);
    }
  }, [live, voiceOn, streamText, agentRunning, duckyVoice, duckySpeed]);

  const toggleLive = () => {
    if (live) {
      stopLiveChat(chatId);
      return;
    }
    const voice = resolveVoiceId(duckyVoice);
    const rate = resolveSpeed(duckySpeed);
    setSessionVoice(voice);
    setSessionSpeed(rate);
    dictation.abort();
    startLiveChat(chatId, { voiceId: voice, speed: rate, isGroup });
  };

  const micTitle = dictation.error
    ? dictation.error
    : live
      ? muted
        ? "Unmute mic — type-only right now"
        : liveMode.error || "Mute mic — keep hearing replies"
      : dictation.isRecording
        ? "Stop recording"
        : dictation.status === "transcribing"
          ? "Transcribing…"
          : "Dictate with microphone";

  return (
    <div className="voice-controls">
      <button
        type="button"
        className={`voice-btn${
          live
            ? muted
              ? " voice-btn--muted"
              : " voice-btn--recording"
            : dictation.isRecording
              ? " voice-btn--recording"
              : ""
        }${dictation.status === "transcribing" ? " voice-btn--busy" : ""}`}
        title={micTitle}
        disabled={!!disabled || (!live && dictation.status === "transcribing")}
        onClick={() => {
          if (live) {
            setMuted((m) => !m);
            return;
          }
          void dictation.toggle();
        }}
        aria-label={live ? (muted ? "Unmute microphone" : "Mute microphone") : "Dictate"}
      >
        {live && muted ? <Icons.MicOff /> : <Icons.Mic />}
      </button>
      <button
        type="button"
        className={`voice-btn${live ? " voice-btn--live" : ""}`}
        title={live ? "Exit live voice mode" : "Live voice mode"}
        disabled={!!disabled}
        onClick={toggleLive}
        aria-label="Live voice"
      >
        <Icons.Headphones />
      </button>
      {dictation.error && !live ? (
        <span className="voice-error" title={dictation.error}>
          !
        </span>
      ) : null}
      {/* Overlay is rendered in the composer slot by ChatPane when onLiveChange is set. */}
      {!onLiveChange ? (
        <VoiceOverlay
          chatId={chatId}
          open={live}
          onClose={exitLive}
          onBack={() => liveMode.back()}
          onForward={() => liveMode.skip()}
          onNewest={() => liveMode.newest()}
          hasPrev={hasPrev}
          hasNext={hasNext}
          hasNewer={hasNewer}
          voiceId={sessionVoice}
          speed={sessionSpeed}
          setVoiceId={setVoiceId}
          setSpeed={setSpeed}
          processTalk={processTalk}
          setProcessTalk={setProcessTalk}
          muted={muted}
        />
      ) : null}
    </div>
  );
}

/** Speaker / pause / restart controls for an assistant message bubble. */
export function SpeakMessageButton({
  text,
  voiceId,
  speed,
}: {
  text: string;
  voiceId?: string;
  speed?: number;
}) {
  const [progress, setProgress] = useState<TtsProgress>(() => ttsEngine.getProgress());

  useEffect(() => ttsEngine.onProgress(setProgress), []);

  if (!text.trim()) return null;

  const active =
    progress.state !== "idle" &&
    Boolean(mapReadAlong(text, progress.spokenText, progress.sourceText, progress.charIndex));
  const paused = active && progress.state === "paused";
  const speaking = active && progress.state === "speaking";
  const loading = active && progress.loading;

  if (!active) {
    return (
      <button
        type="button"
        className="voice-btn voice-btn--tiny voice-speak-msg"
        title="Speak this reply"
        // Defer so this click cannot land on the Pause control that replaces this button.
        onClick={() => {
          const voice = resolveVoiceId(voiceId);
          const rate = resolveSpeed(speed);
          window.setTimeout(() => ttsEngine.speak(text, voice, rate), 0);
        }}
        aria-label="Speak reply"
      >
        <Icons.Speaker />
      </button>
    );
  }

  return (
    <div className="voice-speak-msg-group">
      <button
        type="button"
        className={`voice-btn voice-btn--tiny voice-speak-msg${speaking ? " voice-btn--speaking" : ""}${
          paused ? " voice-btn--paused" : ""
        }${loading ? " voice-btn--busy" : ""}`}
        title={loading ? "Downloading voice…" : paused ? "Resume" : "Pause"}
        onClick={() => (paused ? ttsEngine.resume() : ttsEngine.pause())}
        aria-label={loading ? "Downloading voice" : paused ? "Resume" : "Pause"}
        disabled={loading}
      >
        {loading ? <Icons.Refresh /> : paused ? <Icons.Play /> : <Icons.Pause />}
      </button>
      {paused ? (
        <button
          type="button"
          className="voice-btn voice-btn--tiny voice-speak-msg"
          title="Restart from beginning"
          onClick={() => ttsEngine.restart(resolveVoiceId(voiceId), resolveSpeed(speed))}
          aria-label="Restart"
        >
          <Icons.Replay />
        </button>
      ) : null}
    </div>
  );
}

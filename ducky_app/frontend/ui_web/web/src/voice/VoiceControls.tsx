import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useUiTarget } from "../ui-targets/registry";

import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import { beginDraft, joinWords, renderDraft, type DictationDraft } from "./composerDraft";
import { getLiveVoiceChatIds } from "./liveChats";
import { interruptLiveSpeak, startLiveChat, stopLiveChat } from "./liveSpeakService";
import { runSpeechErrorAction, speechErrorActionLabel } from "./speechErrors";
import { userAwaitsReply } from "./spokenReplyGate";
import { prewarmSpeech } from "./transcriptionSession";
import { useDictation, type VoiceNotice } from "./useDictation";
import { useIsLiveChat } from "./useLiveChatPresence";
import { useLiveVoiceMode } from "./useLiveVoiceMode";
import { VoiceOverlay } from "./VoiceOverlay";
import { saveAudioSettings } from "./audioSettings";
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
  /** Stop Ducky talking now; listening continues. */
  onStopSpeaking: () => void;
  /** Try the mic again after fixing an error. */
  onRetry: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  hasNewer: boolean;
  /** Send each spoken turn on a pause (off = words go into the chat box). */
  autoSend: boolean;
  setAutoSend: (value: boolean) => void;
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
  /** Dictation finished — the composer can focus and put the caret at the end. */
  onDictationEnd?: (text: string) => void;
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

/** Error / tip bubble with an optional one-click fix. Clears itself (see useVoiceNotice). */
export function VoiceNoticeToast({
  notice,
  onDismiss,
  inline = false,
}: {
  notice: VoiceNotice | null;
  onDismiss: () => void;
  inline?: boolean;
}) {
  if (!notice) return null;
  const isError = notice.kind === "error";
  return (
    <div
      className={`voice-notice voice-notice--${notice.kind}${inline ? " voice-notice--inline" : ""}`}
      role={isError ? "alert" : "status"}
    >
      <span className="voice-notice-icon" aria-hidden>
        {isError ? <Icons.AlertTriangle /> : <Icons.Mic />}
      </span>
      <span className="voice-notice-text">{notice.message}</span>
      {notice.action ? (
        <button
          type="button"
          className="voice-notice-action"
          onClick={() => {
            runSpeechErrorAction(notice.action!);
            onDismiss();
          }}
        >
          {speechErrorActionLabel(notice.action)}
        </button>
      ) : null}
      <button type="button" className="voice-notice-close" aria-label="Dismiss" onClick={onDismiss}>
        <Icons.Close />
      </button>
    </div>
  );
}

function subscribeTtsState(onChange: () => void): () => void {
  return ttsEngine.onStateChange(onChange);
}

function ttsBusyNow(): boolean {
  return ttsEngine.getState() !== "idle";
}

/** Silence Ducky now — including a live chat still narrating from another tab. */
function stopAllSpeech(): void {
  if (getLiveVoiceChatIds().size) interruptLiveSpeak();
  else ttsEngine.cancel();
}

/**
 * Mic + live-mode transport — the only ChatPane voice UI touch point.
 */
export function VoiceControls({
  chatId,
  disabled,
  inputText,
  setInputText,
  onSend,
  onDictationEnd,
  streamText,
  agentRunning,
  duckyVoice,
  duckySpeed,
  isGroup,
  onLiveChange,
}: VoiceControlsProps) {
  const micTargetRef = useUiTarget("chat.composer.mic", {
    kind: "button",
    label: "Microphone",
    route: "chat",
  });
  const liveTargetRef = useUiTarget("chat.composer.live", {
    kind: "button",
    label: "Live voice",
    route: "chat",
  });
  const live = useIsLiveChat(chatId);
  const [voiceOn, setVoiceOn] = useState(() => getVoiceSettings().enabled);
  const [autoSend, setAutoSendState] = useState(() => getVoiceSettings().liveAutoSend);
  const [sessionVoice, setSessionVoice] = useState(() => resolveVoiceId(duckyVoice));
  const [sessionSpeed, setSessionSpeed] = useState(() => resolveSpeed(duckySpeed));
  const [processTalk, setProcessTalkState] = useState(() => getVoiceSettings().processTalk);
  const [muted, setMuted] = useState(false);
  const ttsBusy = useSyncExternalStore(subscribeTtsState, ttsBusyNow, ttsBusyNow);
  const onLiveChangeRef = useRef(onLiveChange);
  onLiveChangeRef.current = onLiveChange;
  const inputTextRef = useRef(inputText);
  inputTextRef.current = inputText;
  const onDictationEndRef = useRef(onDictationEnd);
  onDictationEndRef.current = onDictationEnd;

  useEffect(() => {
    void loadVoiceSettings().then((s) => {
      setVoiceOn(s.enabled);
      setAutoSendState(s.liveAutoSend);
      setProcessTalkState(s.processTalk);
      if (!live) {
        setSessionVoice(resolveVoiceId(duckyVoice));
        setSessionSpeed(resolveSpeed(duckySpeed));
      }
    });
    return subscribeVoiceSettings(() => {
      const s = getVoiceSettings();
      setVoiceOn(s.enabled);
      setAutoSendState(s.liveAutoSend);
      setProcessTalkState(s.processTalk);
    });
  }, [live, duckyVoice, duckySpeed]);

  useEffect(() => {
    if (live) return;
    setSessionVoice(resolveVoiceId(duckyVoice));
    setSessionSpeed(resolveSpeed(duckySpeed));
  }, [live, duckyVoice, duckySpeed]);

  // ── Spoken words → chat box (dictation, and live mode with auto-send off) ──
  const draftRef = useRef<DictationDraft | null>(null);

  const writeDraft = useCallback(() => {
    const draft = draftRef.current;
    if (!draft) return;
    const next = renderDraft(draft, inputTextRef.current);
    inputTextRef.current = next;
    setInputText(next);
  }, [setInputText]);

  const beginComposerDraft = useCallback(() => {
    draftRef.current = beginDraft(inputTextRef.current);
  }, []);

  const endComposerDraft = useCallback(() => {
    const draft = draftRef.current;
    if (!draft) return;
    draft.interim = "";
    writeDraft();
    draftRef.current = null;
    onDictationEndRef.current?.(inputTextRef.current);
  }, [writeDraft]);

  const draftInterim = useCallback(
    (text: string) => {
      const draft = draftRef.current;
      if (!draft || draft.interim === text) return;
      draft.interim = text;
      writeDraft();
    },
    [writeDraft],
  );

  const draftFinal = useCallback(
    (text: string) => {
      const draft = draftRef.current;
      if (!draft) return;
      draft.spoken = joinWords(draft.spoken, text);
      draft.interim = "";
      writeDraft();
    },
    [writeDraft],
  );

  const dictation = useDictation({
    disabled: disabled || live,
    onInterim: draftInterim,
    onFinal: draftFinal,
    onSessionChange: (active) => (active ? beginComposerDraft() : endComposerDraft()),
  });

  // ── Live voice: auto-send a finished turn, or write it into the chat box ──
  const autoSendRef = useRef(autoSend);
  autoSendRef.current = autoSend;
  const turnRef = useRef("");

  useEffect(() => {
    if (!live) {
      turnRef.current = "";
      return;
    }
    if (autoSend) return;
    beginComposerDraft();
    return () => endComposerDraft();
  }, [live, autoSend, beginComposerDraft, endComposerDraft]);

  const liveMode = useLiveVoiceMode({
    enabled: live,
    chatId,
    voiceId: sessionVoice,
    speed: sessionSpeed,
    muted,
    agentRunning,
    isGroup,
    onInterim: (text) => {
      if (!autoSendRef.current) draftInterim(text);
    },
    onTranscript: (text) => {
      if (autoSendRef.current) {
        turnRef.current = joinWords(turnRef.current, text);
        return;
      }
      draftFinal(text);
    },
    onTurnEnd: () => {
      if (!autoSendRef.current) return;
      const text = turnRef.current.trim();
      turnRef.current = "";
      if (text) onSend(text);
    },
  });

  useEffect(() => {
    if (!live) setMuted(false);
  }, [live]);

  const { skip, back, newest, interrupt, retry, hasPrev, hasNext, hasNewer } = liveMode;

  const exitLive = useCallback(() => {
    stopLiveChat(chatId);
  }, [chatId]);

  const setAutoSend = useCallback((value: boolean) => {
    setAutoSendState(value);
    void saveVoiceSettings({ liveAutoSend: value });
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
        onStopSpeaking: interrupt,
        onRetry: retry,
        hasPrev,
        hasNext,
        hasNewer,
        autoSend,
        setAutoSend,
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
    interrupt,
    retry,
    hasPrev,
    hasNext,
    hasNewer,
    autoSend,
    setAutoSend,
    muted,
    sessionVoice,
    sessionSpeed,
    setVoiceId,
    setSpeed,
    processTalk,
    setProcessTalk,
  ]);

  // Speak-along outside live mode when style is speak_along — only for turns the user sent.
  const streamLenRef = useRef(0);
  useEffect(() => {
    if (live || !voiceOn) {
      streamLenRef.current = 0;
      return;
    }
    const settings = getVoiceSettings();
    if (settings.spokenStyle !== "speak_along" || !userAwaitsReply(chatId)) {
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
  }, [chatId, live, voiceOn, streamText, agentRunning, duckyVoice, duckySpeed]);

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

  const micTitle = live
    ? muted
      ? "Unmute mic — type-only right now"
      : "Mute mic — keep hearing replies"
    : dictation.status === "connecting"
      ? "Starting the mic… (click to cancel)"
      : dictation.isRecording
        ? "Stop — your words stay in the box, then press Send"
        : dictation.status === "transcribing"
          ? "Finishing the last words…"
          : "Dictate — talk and your words appear in the box";

  const micClass = `voice-btn${
    live
      ? muted
        ? " voice-btn--muted"
        : " voice-btn--recording"
      : dictation.isRecording
        ? " voice-btn--recording"
        : ""
  }${!live && dictation.isBusy ? " voice-btn--busy" : ""}`;

  return (
    <div className="voice-controls">
      {!live && dictation.notice ? (
        <VoiceNoticeToast notice={dictation.notice} onDismiss={dictation.dismissNotice} />
      ) : null}
      {ttsBusy && !live ? (
        <button
          type="button"
          className="voice-btn voice-btn--stop-speaking"
          title="Stop speaking"
          aria-label="Stop speaking"
          onClick={stopAllSpeech}
        >
          <Icons.Stop />
        </button>
      ) : null}
      <button
        ref={micTargetRef}
        type="button"
        className={micClass}
        title={micTitle}
        disabled={!!disabled || (!live && dictation.status === "transcribing")}
        onPointerEnter={() => {
          if (!live && !disabled) prewarmSpeech();
        }}
        onClick={() => {
          if (live) {
            setMuted((m) => !m);
            return;
          }
          void dictation.toggle();
        }}
        aria-label={
          live
            ? muted
              ? "Unmute microphone"
              : "Mute microphone"
            : dictation.isRecording
              ? "Stop dictation"
              : "Dictate"
        }
        aria-pressed={!live ? dictation.isRecording : undefined}
      >
        {live && muted ? <Icons.MicOff /> : !live && dictation.isBusy ? <Icons.Spinner /> : <Icons.Mic />}
      </button>
      <button
        ref={liveTargetRef}
        type="button"
        className={`voice-btn${live ? " voice-btn--live" : ""}`}
        title={live ? "Exit live voice mode" : "Live voice — talk back and forth with Ducky"}
        disabled={!!disabled}
        onPointerEnter={() => {
          if (!live && !disabled) prewarmSpeech();
        }}
        onClick={toggleLive}
        aria-label="Live voice"
      >
        <Icons.Headphones />
      </button>
      {/* Overlay is rendered in the composer slot by ChatPane when onLiveChange is set. */}
      {!onLiveChange ? (
        <VoiceOverlay
          chatId={chatId}
          open={live}
          onClose={exitLive}
          onBack={() => liveMode.back()}
          onForward={() => liveMode.skip()}
          onNewest={() => liveMode.newest()}
          onStopSpeaking={interrupt}
          onRetry={retry}
          hasPrev={hasPrev}
          hasNext={hasNext}
          hasNewer={hasNewer}
          autoSend={autoSend}
          setAutoSend={setAutoSend}
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

/** Speak / pause / resume / stop controls for an assistant message bubble. */
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
  const loading = active && progress.loading && !paused;

  if (!active) {
    const error = progress.sourceText === text.trim() ? progress.error : "";
    return (
      <>
        <button
          type="button"
          className="voice-btn voice-btn--tiny voice-speak-msg"
          title={error || "Read this reply aloud"}
          onClick={() => {
            const voice = resolveVoiceId(voiceId);
            const rate = resolveSpeed(speed);
            ttsEngine.speak(text, voice, rate);
          }}
          aria-label="Read reply aloud"
        >
          <Icons.Speaker />
        </button>
        {error ? (
          <span className="voice-notice voice-notice--error voice-notice--inline" role="alert">
            <span className="voice-notice-text">{error}</span>
            {progress.errorCode === "audio_muted" ? (
              <button type="button" className="voice-notice-action" title="Unmute Ducky and read this reply" aria-label="Unmute Ducky"
                onClick={() => {
                  void saveAudioSettings({ audioMuted: false }).then(() =>
                    ttsEngine.speak(text, resolveVoiceId(voiceId), resolveSpeed(speed)),
                  );
                }}>
                <Icons.Speaker />
              </button>
            ) : null}
            {progress.errorCode === "voice_missing" ? (
              <button type="button" className="voice-notice-action" title="Download a Windows voice" aria-label="Download a Windows voice"
                onClick={() => void getApi()?.voice_open_windows_settings?.("voice_download")}>
                <Icons.Download />
              </button>
            ) : null}
          </span>
        ) : null}
      </>
    );
  }

  return (
    <div className="voice-speak-msg-group" role="group" aria-label="Reading aloud">
      <button
        type="button"
        className={`voice-btn voice-btn--tiny voice-speak-msg${paused ? " voice-btn--paused" : " voice-btn--speaking"}${
          loading ? " voice-btn--busy" : ""
        }`}
        title={paused ? "Resume" : loading ? `${progress.loadingMessage || "Preparing voice…"} (click to pause)` : "Pause"}
        onClick={() => (paused ? ttsEngine.resume() : ttsEngine.pause())}
        aria-label={paused ? "Resume" : "Pause"}
      >
        {paused ? <Icons.Play /> : loading ? <Icons.Spinner /> : <Icons.Pause />}
      </button>
      <button
        type="button"
        className="voice-btn voice-btn--tiny voice-speak-msg voice-speak-msg--stop"
        title="Stop reading"
        onClick={() => ttsEngine.cancel()}
        aria-label="Stop reading"
      >
        <Icons.Stop />
      </button>
      {paused ? (
        <button
          type="button"
          className="voice-btn voice-btn--tiny voice-speak-msg"
          title="Restart from the beginning"
          onClick={() => ttsEngine.restart(resolveVoiceId(voiceId), resolveSpeed(speed))}
          aria-label="Restart"
        >
          <Icons.Replay />
        </button>
      ) : null}
    </div>
  );
}

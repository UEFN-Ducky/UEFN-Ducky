import { useEffect, useState } from "react";

import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import {
  getLiveVoiceState,
  subscribeLiveVoiceChats,
  type LiveVoiceState,
  type LiveVoiceUiStatus,
} from "./liveChats";
import { LiveVoicePickers } from "./LiveVoicePickers";
import { runSpeechErrorAction, speechErrorActionLabel } from "./speechErrors";
import { ttsEngine, type TtsProgress } from "./ttsEngine";

export type VoiceOverlayProps = {
  chatId: string;
  open: boolean;
  onClose: () => void;
  onBack: () => void;
  onForward: () => void;
  onNewest: () => void;
  /** Stop Ducky talking now; listening continues. */
  onStopSpeaking?: () => void;
  /** Reopen the mic after an error. */
  onRetry?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  hasNewer?: boolean;
  /** Send each spoken turn on a pause (off = words go into the chat box). */
  autoSend?: boolean;
  setAutoSend?: (value: boolean) => void;
  voiceId?: string;
  speed?: number;
  setVoiceId?: (value: string) => void;
  setSpeed?: (value: number) => void;
  processTalk?: number;
  setProcessTalk?: (value: number) => void;
  /** Sit above the composer textarea instead of floating over the chat. */
  inline?: boolean;
  /** When false, voice/speed pickers stay off this panel. */
  showPickers?: boolean;
  /** Mic off — type only; replies still speak. */
  muted?: boolean;
  chatModel?: string;
  setChatModel?: (value: string) => void;
  codingAgent?: string;
  setCodingAgent?: (value: string) => void;
  showChatModel?: boolean;
};

function statusLabel(status: LiveVoiceUiStatus, loadingVoice: boolean, muted: boolean): string {
  if (status === "error") return "Mic problem";
  if (status === "connecting") return "Starting the mic…";
  if (loadingVoice) return "Loading voice…";
  if (status === "thinking") return "Thinking…";
  if (status === "speaking") return "Speaking…";
  if (muted || status === "muted") return "Muted — type to chat";
  if (status === "listening") return "Listening…";
  return "";
}

/**
 * Live-voice panel. Inline mode slides in above the composer; floating is legacy.
 */
export function VoiceOverlay({
  chatId,
  open,
  onClose,
  onBack,
  onForward,
  onNewest,
  onStopSpeaking,
  onRetry,
  hasPrev = false,
  hasNext = false,
  hasNewer = false,
  autoSend = false,
  setAutoSend,
  voiceId = "",
  speed = 1,
  setVoiceId,
  setSpeed,
  processTalk,
  setProcessTalk,
  inline = false,
  showPickers = true,
  muted = false,
  chatModel,
  setChatModel,
  codingAgent,
  setCodingAgent,
  showChatModel = false,
}: VoiceOverlayProps) {
  const [state, setState] = useState<LiveVoiceState>(() => getLiveVoiceState(chatId));
  const [tts, setTts] = useState<TtsProgress>(() => ttsEngine.getProgress());

  useEffect(() => {
    setState(getLiveVoiceState(chatId));
    return subscribeLiveVoiceChats(() => setState(getLiveVoiceState(chatId)));
  }, [chatId]);

  useEffect(() => ttsEngine.onProgress(setTts), []);

  if (!open) return null;

  const loadingVoice = tts.loading;
  const micMuted = muted || state.muted || state.status === "muted";
  const label = statusLabel(state.status, loadingVoice, micMuted);
  const pickers = showPickers && Boolean(setVoiceId && setSpeed);
  const busyOrb =
    state.status === "thinking" ||
    state.status === "speaking" ||
    state.status === "error" ||
    state.status === "connecting";
  const orbStatus = loadingVoice
    ? "thinking"
    : busyOrb
      ? state.status === "connecting"
        ? "thinking"
        : state.status
      : micMuted
        ? "muted"
        : state.status;
  const speaking = tts.state === "speaking";
  const paused = tts.state === "paused";
  const talking = speaking || paused;
  const heard = state.status !== "error" ? state.userInterim.trim() : "";

  return (
    <div
      className={`voice-overlay${inline ? " voice-overlay--composer" : ""}`}
      role="dialog"
      aria-label="Live voice"
    >
      <div className={`voice-overlay-card voice-overlay-card--${orbStatus}`}>
        <div className="voice-overlay-top">
          <div className="voice-overlay-identity">
            <div className={`voice-overlay-orb-wrap voice-overlay-orb-wrap--${orbStatus}`} aria-hidden>
              <span className="voice-overlay-orb-ping" />
              <span className={`voice-overlay-orb voice-overlay-orb--${orbStatus}`} />
            </div>
            <div className="voice-overlay-copy">
              <div
                className={`voice-overlay-status${orbStatus === "listening" ? " voice-overlay-status--live" : ""}`}
                aria-live="polite"
              >
                {label}
              </div>
              {heard ? <div className="voice-overlay-heard">“{heard}”</div> : null}
              {state.nextSpeaker ? <div className="voice-overlay-next">{state.nextSpeaker}</div> : null}
              {loadingVoice && tts.loadingMessage ? (
                <div className="voice-overlay-next" role="status">{tts.loadingMessage}</div>
              ) : null}
              {tts.error ? (
                <div className="voice-overlay-next" role="alert">
                  {tts.error}
                  {tts.errorCode === "voice_missing" ? (
                    <button type="button" className="voice-btn" title="Download a Windows voice" aria-label="Download a Windows voice"
                      onClick={() => void getApi()?.voice_open_windows_settings?.("voice_download")}>
                      <Icons.Download />
                    </button>
                  ) : null}
                </div>
              ) : null}
              {state.status !== "error" && state.notice ? (
                <div className="voice-overlay-next">{state.notice}</div>
              ) : null}
            </div>
          </div>
          <div className="voice-overlay-transport">
            <button
              type="button"
              className="voice-btn voice-btn--tiny"
              title="Previous line"
              onClick={onBack}
              disabled={!hasPrev}
            >
              <Icons.Back />
            </button>
            <button
              type="button"
              className="voice-btn voice-btn--tiny"
              title={paused ? "Resume" : speaking ? "Pause" : "Play"}
              disabled={!talking && !hasPrev && !hasNext}
              onClick={() => {
                if (paused) ttsEngine.resume();
                else if (talking) ttsEngine.pause();
                else if (hasPrev) onBack();
                else onForward();
              }}
            >
              {paused || !speaking ? <Icons.Play /> : <Icons.Pause />}
            </button>
            <button
              type="button"
              className="voice-btn voice-btn--tiny"
              title="Stop talking"
              aria-label="Stop talking"
              disabled={!talking && !loadingVoice}
              onClick={() => (onStopSpeaking ? onStopSpeaking() : ttsEngine.cancel())}
            >
              <Icons.Stop />
            </button>
            <button
              type="button"
              className="voice-btn voice-btn--tiny"
              title="Next line"
              onClick={onForward}
              disabled={!hasNext}
            >
              <Icons.Skip />
            </button>
            {hasNewer ? (
              <button
                type="button"
                className="voice-btn voice-btn--tiny"
                title="Newest line"
                onClick={onNewest}
              >
                <Icons.SkipToEnd />
              </button>
            ) : null}
            <span className="voice-overlay-transport-split" aria-hidden />
            {setAutoSend ? (
              <button
                type="button"
                className={`voice-overlay-manual-toggle${autoSend ? " is-on" : ""}`}
                aria-pressed={autoSend}
                title={
                  autoSend
                    ? "Auto-send on: each pause sends what you said"
                    : "Auto-send off: what you say goes into the box — press Send"
                }
                onClick={() => setAutoSend(!autoSend)}
              >
                Auto-send
              </button>
            ) : null}
            <button
              type="button"
              className="voice-btn voice-btn--tiny voice-overlay-exit"
              title="Exit live voice"
              onClick={onClose}
            >
              <Icons.Close />
            </button>
          </div>
        </div>
        {state.status === "error" && state.error ? (
          <div className="voice-notice voice-notice--error voice-notice--inline" role="alert">
            <span className="voice-notice-icon" aria-hidden>
              <Icons.AlertTriangle />
            </span>
            <span className="voice-notice-text">{state.error}</span>
            {state.errorAction ? (
              <button
                type="button"
                className="voice-notice-action"
                onClick={() => runSpeechErrorAction(state.errorAction!)}
              >
                {speechErrorActionLabel(state.errorAction)}
              </button>
            ) : null}
            {onRetry ? (
              <button type="button" className="voice-notice-action" onClick={onRetry}>
                Try again
              </button>
            ) : null}
          </div>
        ) : null}
        {pickers && setVoiceId && setSpeed ? (
          <div className="voice-overlay-controls">
            <LiveVoicePickers
              voiceId={voiceId}
              speed={speed}
              setVoiceId={setVoiceId}
              setSpeed={setSpeed}
              processTalk={processTalk}
              setProcessTalk={setProcessTalk}
              chatModel={chatModel}
              setChatModel={setChatModel}
              codingAgent={codingAgent}
              setCodingAgent={setCodingAgent}
              showChatModel={showChatModel}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

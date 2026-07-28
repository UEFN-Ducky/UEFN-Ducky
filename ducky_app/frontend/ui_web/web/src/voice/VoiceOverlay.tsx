import { useEffect, useState } from "react";

import { Icons } from "../icons/Icons";
import {
  getLiveVoiceState,
  subscribeLiveVoiceChats,
  type LiveVoiceState,
  type LiveVoiceUiStatus,
} from "./liveChats";
import { LiveVoicePickers } from "./LiveVoicePickers";
import { ttsEngine } from "./ttsEngine";

export type VoiceOverlayProps = {
  chatId: string;
  open: boolean;
  onClose: () => void;
  onSkip: () => void;
  onReplay: () => void;
  onSend?: () => void;
  canSend?: boolean;
  manualSend?: boolean;
  setManualSend?: (value: boolean) => void;
  voiceId?: string;
  speed?: number;
  setVoiceId?: (value: string) => void;
  setSpeed?: (value: number) => void;
  /** Fill the composer slot instead of floating over the chat. */
  inline?: boolean;
  /** When false, voice/speed/auto-send are rendered elsewhere (composer toolbar). */
  showPickers?: boolean;
};

function statusLabel(
  status: LiveVoiceUiStatus,
  error: string,
  manualSend: boolean,
  loadingVoice: boolean,
): string {
  if (status === "error") return error || "Something went wrong";
  if (loadingVoice) return "Downloading voice…";
  if (status === "thinking") return "Thinking…";
  if (status === "speaking") return "Speaking…";
  if (status === "listening") {
    return manualSend ? "Listening… (press Send when ready)" : "Listening…";
  }
  return "";
}

/**
 * Live-voice panel. Inline mode replaces the composer textarea; floating is legacy.
 */
export function VoiceOverlay({
  chatId,
  open,
  onClose,
  onSkip,
  onReplay,
  onSend,
  canSend = false,
  manualSend = false,
  setManualSend,
  voiceId = "",
  speed = 1,
  setVoiceId,
  setSpeed,
  inline = false,
  showPickers = true,
}: VoiceOverlayProps) {
  const [state, setState] = useState<LiveVoiceState>(() => getLiveVoiceState(chatId));
  const [loadingVoice, setLoadingVoice] = useState(() => ttsEngine.getProgress().loading);

  useEffect(() => {
    setState(getLiveVoiceState(chatId));
    return subscribeLiveVoiceChats(() => setState(getLiveVoiceState(chatId)));
  }, [chatId]);

  useEffect(() => ttsEngine.onProgress((p) => setLoadingVoice(p.loading)), []);

  if (!open) return null;

  const label = statusLabel(state.status, state.error, manualSend, loadingVoice);
  const youText = state.userInterim || state.lastUserText;
  const duckyText = state.spokenText;
  const pickers = showPickers && Boolean(setVoiceId && setSpeed);
  const orbStatus = loadingVoice ? "thinking" : state.status;

  return (
    <div
      className={`voice-overlay${inline ? " voice-overlay--composer" : ""}`}
      role="dialog"
      aria-label="Live voice"
    >
      <div className={`voice-overlay-card voice-overlay-card--${orbStatus}`}>
        <div className={`voice-overlay-orb voice-overlay-orb--${orbStatus}`} aria-hidden />
        <div className="voice-overlay-body">
          <div className="voice-overlay-status" aria-live="polite">
            {label}
          </div>
          {youText ? (
            <div className="voice-overlay-line">
              <span className="voice-overlay-who">You</span>
              <span
                className={`voice-overlay-text${state.userInterim ? " voice-overlay-text--interim" : ""}`}
              >
                {youText}
              </span>
            </div>
          ) : null}
          {duckyText ? (
            <div className="voice-overlay-line">
              <span className="voice-overlay-who">{state.speakerName || "Ducky"}</span>
              <span className="voice-overlay-text">{duckyText}</span>
            </div>
          ) : null}
          {state.nextSpeaker ? (
            <div className="voice-overlay-next">{state.nextSpeaker}</div>
          ) : null}
          {state.status === "error" && state.error ? (
            <div className="voice-overlay-error">{state.error}</div>
          ) : null}
          {pickers && setVoiceId && setSpeed ? (
            <div className="voice-overlay-controls">
              <LiveVoicePickers
                voiceId={voiceId}
                speed={speed}
                setVoiceId={setVoiceId}
                setSpeed={setSpeed}
                manualSend={manualSend}
                setManualSend={setManualSend}
              />
            </div>
          ) : null}
        </div>
        <div className="voice-overlay-actions">
          {manualSend && onSend ? (
            <button
              type="button"
              className="voice-btn voice-btn--tiny voice-btn--send"
              title="Send what you said"
              disabled={!canSend}
              onClick={onSend}
            >
              <Icons.Send />
            </button>
          ) : null}
          <button type="button" className="voice-btn voice-btn--tiny" title="Replay" onClick={onReplay}>
            <Icons.Replay />
          </button>
          <button type="button" className="voice-btn voice-btn--tiny" title="Skip speech" onClick={onSkip}>
            <Icons.Skip />
          </button>
          <button type="button" className="voice-btn voice-btn--tiny" title="Exit live voice" onClick={onClose}>
            <Icons.Close />
          </button>
        </div>
      </div>
    </div>
  );
}

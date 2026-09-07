import { memo, useCallback, useSyncExternalStore } from "react";
import type { MessageAuthorDto } from "../types/panel";
import type { OpenFileHandler } from "../types/richContent";
import { SpeakMessageButton } from "../voice/VoiceControls";
import { mapReadAlong, TtsReadAlong } from "../voice/TtsReadAlong";
import { ttsEngine, type TtsProgress } from "../voice/ttsEngine";
import { RichContentRenderer } from "./rich-content/RichContentRenderer";
import { ThinkingBlock } from "./ThinkingBlock";

interface MessageBubbleProps {
  role: string;
  text: string;
  isStreaming?: boolean;
  thinking?: string;
  incomplete?: boolean;
  error?: string;
  onOpenFile?: OpenFileHandler;
  /** Per-ducky TTS voice id (optional). */
  voiceId?: string;
  /** Per-ducky talking speed (optional; 0 → global default). */
  speed?: number;
  /** Group-chat speaker (name/color/voice). */
  author?: MessageAuthorDto;
  /** Stop the live run (shown on collapsed thinking while streaming). */
  onStop?: () => void;
  /** Resume this interrupted turn without sending a new user message. */
  onContinue?: () => void;
  /** Play-audio control — only the latest assistant reply should pass true. */
  showSpeakButton?: boolean;
}

const IDLE_TTS: TtsProgress = { state: "idle", sourceText: "", spokenText: "", charIndex: 0, loading: false };

function subscribeTts(onChange: () => void): () => void {
  return ttsEngine.onProgress(onChange);
}

// User messages render via EditableUserMessage (as sticky group headers); this
// component now only renders assistant bubbles (streamed answers + reasoning).
export const MessageBubble = memo(function MessageBubble({
  role,
  text,
  isStreaming,
  thinking,
  incomplete,
  error,
  onOpenFile,
  voiceId,
  speed,
  author,
  onStop,
  onContinue,
  showSpeakButton = false,
}: MessageBubbleProps) {
  // Every bubble used to hold its own TTS subscription and re-render on every
  // word boundary while anything was being spoken. Select instead: bubbles that
  // are not the one being read get the shared IDLE snapshot and never re-render.
  const selectTts = useCallback((): TtsProgress => {
    const p = ttsEngine.getProgress();
    if (p.state === "idle") return IDLE_TTS;
    return mapReadAlong(text, p.spokenText, p.sourceText, p.charIndex) ? p : IDLE_TTS;
  }, [text]);
  const tts = useSyncExternalStore(subscribeTts, selectTts, selectTts);

  if (role === "user") return null;

  const readAlong =
    tts.state !== "idle" ? mapReadAlong(text, tts.spokenText, tts.sourceText, tts.charIndex) : null;
  const ttsActive = Boolean(readAlong);
  const speakVoice = author?.tts_voice || voiceId;
  const speakSpeed = author?.tts_speed ?? speed;
  const authorName = author?.name?.trim();

  return (
    <div
      className={`message-bubble-assistant-row${isStreaming ? " message-bubble-assistant-row--streaming" : ""}${
        ttsActive ? " message-bubble-assistant-row--tts-active" : ""
      }`}
    >
      <div className="message-bubble-assistant-content">
        {authorName ? (
          <div
            className="message-bubble-author"
            style={{ ["--member-color" as string]: author?.color || "var(--accent)" }}
          >
            {authorName}
          </div>
        ) : null}
        {thinking ? (
          <ThinkingBlock
            text={thinking}
            isStreaming={isStreaming && !text}
            interrupted={incomplete}
            onStop={isStreaming ? onStop : undefined}
          />
        ) : null}
        {readAlong ? (
          <TtsReadAlong spokenText={readAlong.spokenText} charIndex={readAlong.charIndex} />
        ) : (
          <RichContentRenderer
            text={text}
            onOpenFile={onOpenFile}
            mode={isStreaming ? "streaming" : "full"}
          />
        )}
        {isStreaming && text ? <span className="message-bubble-stream-cursor" aria-hidden="true" /> : null}
        {incomplete ? (
          <div className="message-bubble-interrupted" role="alert">
            <span className="message-bubble-interrupted-icon" aria-hidden="true">⚠</span>
            <span>{error ? `Interrupted: ${error}` : "Interrupted before finishing"}</span>
            {onContinue ? (
              <button
                type="button"
                className="message-bubble-interrupted-continue"
                onClick={onContinue}
              >
                Continue
              </button>
            ) : null}
          </div>
        ) : null}
        {showSpeakButton && !isStreaming && text.trim() ? (
          <div className="message-bubble-voice-actions">
            <SpeakMessageButton text={text} voiceId={speakVoice} speed={speakSpeed} />
          </div>
        ) : null}
      </div>
    </div>
  );
});

import { memo, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { MessageAttachmentDto, MessageAuthorDto } from "../types/panel";
import type { OpenFileHandler } from "../types/richContent";
import { SpeakMessageButton } from "../voice/VoiceControls";
import { clearSpokenWord, mapReadAlong, markSpokenWord } from "../voice/TtsReadAlong";
import { ttsEngine, type TtsProgress } from "../voice/ttsEngine";
import { ModelSelector } from "./ModelSelector";
import { RichContentRenderer } from "./rich-content/RichContentRenderer";
import { ThinkingBlock } from "./ThinkingBlock";
import { FileProtectionRecovery } from "./FileProtectionRecovery";
import { AttachmentStrip } from "./EditableUserMessage";
import { AttachmentPreviewModal } from "./AttachmentPreviewModal";
import { MediaLightbox } from "./MediaLightbox";
import { openPanelRoute } from "../navigation/openPanelRoute";
import { TourOffer } from "../walkthrough/TourOffer";
import { useIntroTyping } from "../walkthrough/introTyping";

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
  /** Same picker as the composer — pick a model, then Continue. */
  selectedModel?: string;
  setSelectedModel?: (id: string) => void;
  codingAgent?: string;
  setCodingAgent?: (id: string) => void;
  convId?: string;
  /** Play-audio control — only the latest assistant reply should pass true. */
  showSpeakButton?: boolean;
  /** Pictures the agent made this turn (image tools or its own image generation). */
  attachments?: MessageAttachmentDto[];
  /** Walkthroughs offered as Start buttons under the text (the welcome ducky). */
  tours?: string[];
}

/** Media Kit takes the file; its tab listens for this event once it is open. */
function sendToMediaKit(action: "open" | "export", a: MessageAttachmentDto) {
  openPanelRoute("settings.tab", "Island Media Kit");
  window.dispatchEvent(
    new CustomEvent("ducky:media-kit", { detail: { action, kind: a.kind, name: a.name, path: a.path ?? "" } }),
  );
}

/** A video, sound or motion scene an agent made: plays right under the reply. */
function ReplyMediaCard({ media }: { media: MessageAttachmentDto }) {
  const [big, setBig] = useState(false);
  const url = media.media_url;
  if (!url) return null;
  const isMp4 = media.kind === "video" && /\.mp4$/i.test(media.name);
  return (
    <div className={`message-bubble-media-card message-bubble-media-card--${media.kind}`}>
      {media.kind === "video" ? <video src={url} controls preload="metadata" /> : null}
      {media.kind === "video" ? (
        <MediaLightbox items={[{ kind: "video", name: media.name, url }]} index={big ? 0 : null} onClose={() => setBig(false)} />
      ) : null}
      {media.kind === "audio" ? <audio src={url} controls preload="metadata" /> : null}
      {media.kind === "motion" ? (
        // No same-origin: the scene's script can't reach the app; the server also sends a self-only CSP.
        <iframe src={url} sandbox="allow-scripts" title={media.name} loading="lazy" />
      ) : null}
      <div className="message-bubble-media-actions">
        {media.kind === "video" ? (
          <button type="button" onClick={() => setBig(true)}>
            Open big
          </button>
        ) : null}
        <button type="button" onClick={() => sendToMediaKit("open", media)}>
          Open in Media Kit
        </button>
        {media.kind === "audio" ? null : isMp4 ? (
          <a href={url} download={media.name}>
            Export MP4
          </a>
        ) : (
          <button type="button" onClick={() => sendToMediaKit("export", media)}>
            Export MP4
          </button>
        )}
        <a href={url} download={media.name}>
          Save
        </a>
      </div>
    </div>
  );
}

function ReplyPictures({ attachments }: { attachments: MessageAttachmentDto[] }) {
  const [preview, setPreview] = useState<MessageAttachmentDto | null>(null);
  const images = attachments.filter((a) => a.kind === "image");
  const media = attachments.filter((a) => a.kind === "video" || a.kind === "audio" || a.kind === "motion");
  if (!images.length && !media.length) return null;
  return (
    <div className="message-bubble-reply-pictures">
      {images.length ? <AttachmentStrip images={images} files={[]} videos={[]} onPreview={setPreview} /> : null}
      {media.map((m) => (
        <ReplyMediaCard key={m.generated || m.name} media={m} />
      ))}
      <AttachmentPreviewModal open={preview !== null} attachment={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

const IDLE_TTS: TtsProgress = { state: "idle", sourceText: "", spokenText: "", charIndex: 0, loading: false };

function subscribeTts(onChange: () => void): () => void {
  return ttsEngine.onProgress(onChange);
}

// User messages render via EditableUserMessage (as sticky group headers); this
// component now only renders assistant bubbles (streamed answers + reasoning).
export const MessageBubble = memo(function MessageBubble({
  role,
  text: fullText,
  isStreaming: streaming,
  thinking,
  incomplete,
  error,
  onOpenFile,
  voiceId,
  speed,
  author,
  onStop,
  onContinue,
  selectedModel = "",
  setSelectedModel,
  codingAgent,
  setCodingAgent,
  convId,
  showSpeakButton = false,
  attachments,
  tours,
}: MessageBubbleProps) {
  // Every bubble used to hold its own TTS subscription and re-render on every
  // word boundary while anything was being spoken. Select instead: bubbles that
  // are not the one being read get the shared IDLE snapshot and never re-render.
  const selectTts = useCallback((): TtsProgress => {
    const p = ttsEngine.getProgress();
    if (p.state === "idle") return IDLE_TTS;
    return mapReadAlong(fullText, p.spokenText, p.sourceText, p.charIndex) ? p : IDLE_TTS;
  }, [fullText]);
  const tts = useSyncExternalStore(subscribeTts, selectTts, selectTts);
  // The welcome ducky's first message types itself out right after the setup.
  const introShown = useIntroTyping(fullText, role !== "user" && !!tours?.length);
  const text = introShown ?? fullText;
  const isStreaming = introShown !== null || streaming;

  const readAlong =
    tts.state !== "idle" ? mapReadAlong(text, tts.spokenText, tts.sourceText, tts.charIndex) : null;
  const ttsActive = Boolean(readAlong);
  // Read-aloud keeps the formatted reply on screen and marks the spoken word in it
  // (it used to swap to the raw markdown text with ## and ** while speaking).
  const richRef = useRef<HTMLDivElement>(null);
  const markedRef = useRef(false);
  useEffect(() => {
    if (!readAlong) {
      if (markedRef.current) clearSpokenWord();
      markedRef.current = false;
      return;
    }
    markedRef.current = markSpokenWord(richRef.current, readAlong.spokenText, readAlong.charIndex);
  }, [readAlong?.spokenText, readAlong?.charIndex]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    if (markedRef.current) clearSpokenWord();
  }, []);

  if (role === "user") return null;

  const speakVoice = author?.tts_voice || voiceId;
  const speakSpeed = author?.tts_speed ?? speed;
  // Group chats: which group (outer to inner) the speaking member works in.
  const authorPath = (author?.group_path ?? []).map((g) => g.trim()).filter(Boolean);
  let authorName = author?.name?.trim();
  for (const group of authorPath) {
    if (authorName?.startsWith(`${group} — `)) authorName = authorName.slice(group.length + 3);
  }

  return (
    <div
      className={`message-bubble-assistant-row${isStreaming ? " message-bubble-assistant-row--streaming" : ""}${
        ttsActive ? " message-bubble-assistant-row--tts-active" : ""
      }${tours?.length ? " message-bubble-assistant-row--welcome" : ""}`}
    >
      <div className="message-bubble-assistant-content">
        {authorName ? (
          <div
            className="message-bubble-author"
            style={{ ["--member-color" as string]: author?.color || "var(--accent)" }}
          >
            {authorPath.length ? (
              <span className="message-bubble-author-path">{authorPath.join(" › ")} › </span>
            ) : null}
            {authorName}
          </div>
        ) : null}
        {thinking ? (
          <ThinkingBlock
            text={thinking}
            isStreaming={isStreaming && !text}
            interrupted={incomplete && !isStreaming}
            onStop={isStreaming ? onStop : undefined}
          />
        ) : null}
        <div ref={richRef} className="message-bubble-rich">
          <RichContentRenderer
            text={text}
            onOpenFile={onOpenFile}
            mode={isStreaming ? "streaming" : "full"}
          />
        </div>
        {isStreaming && text ? <span className="message-bubble-stream-cursor" aria-hidden="true" /> : null}
        {attachments?.length ? <ReplyPictures attachments={attachments} /> : null}
        {tours?.length && !isStreaming ? <TourOffer ids={tours} /> : null}
        {incomplete && !isStreaming ? (
          <div className="message-bubble-interrupted" role="alert">
            <span className="message-bubble-interrupted-icon" aria-hidden="true">⚠</span>
            <span className="message-bubble-interrupted-msg">
              {error ? `Interrupted: ${error}` : "Interrupted before finishing"}
            </span>
            {error?.includes("AI_FILE_PROTECTION:") ? <FileProtectionRecovery /> : null}
            {onContinue ? (
              <div className="message-bubble-interrupted-actions">
                {setSelectedModel ? (
                  <ModelSelector
                    selectedModel={selectedModel}
                    setSelectedModel={setSelectedModel}
                    codingAgent={codingAgent}
                    setCodingAgent={setCodingAgent}
                    convId={convId}
                    preserveSelection
                    menuPlacement="bottom"
                  />
                ) : null}
                <button
                  type="button"
                  className="message-bubble-interrupted-continue"
                  onClick={onContinue}
                >
                  Continue
                </button>
              </div>
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

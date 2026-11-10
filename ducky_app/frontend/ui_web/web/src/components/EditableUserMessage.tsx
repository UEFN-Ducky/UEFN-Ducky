import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { AgentMode, MessageAttachmentDto } from "../types/panel";
import { Icons } from "../icons/Icons";
import { AttachmentPreviewModal } from "./AttachmentPreviewModal";
import { ModeSelector } from "./ModeSelector";
import { ModelSelector } from "./ModelSelector";
import { InlineStopButton } from "./InlineStopButton";
import { ChatRefText } from "./ChatRefText";
import { PromptCopyButton } from "./PromptCopyButton";

// The editor opens at ~3 lines and auto-grows to fit its content up to a cap.
const MIN_EDIT_HEIGHT = 66;
const MAX_EDIT_HEIGHT = 520;

interface EditableUserMessageProps {
  text: string;
  attachments?: MessageAttachmentDto[];
  /** Only the most recent user message is editable/resendable. */
  editable?: boolean;
  /** Pane's current composer selections, used to seed the inline editor. */
  currentMode: AgentMode;
  currentModel: string;
  /** Per-chat backend so the edit toolbar can switch Ducky ⇄ Claude/Codex/Cursor. */
  codingAgent?: string;
  setCodingAgent?: (id: string) => void;
  convId?: string;
  onResend: (text: string, mode: AgentMode, model: string, attachments?: MessageAttachmentDto[]) => void;
  /** Stop the live run (Cursor: stop from the sticky last question). */
  onStop?: () => void;
  /** Restart from here: this message back in the composer, it and everything after erased. */
  onRestart?: () => void;
}

function attachmentSrc(att: MessageAttachmentDto): string | null {
  if (att.kind === "image" && att.media_url) return att.media_url;
  if (att.kind !== "image" || !att.data_base64) return null;
  const mime = att.mime || "image/png";
  return `data:${mime};base64,${att.data_base64}`;
}

/** Thumbnails/chips for the message's attachments; `onRemove` adds an ✕ to each (editing). */
export function AttachmentStrip({
  images,
  files,
  videos,
  onPreview,
  onRemove,
}: {
  images: MessageAttachmentDto[];
  files: MessageAttachmentDto[];
  videos: MessageAttachmentDto[];
  onPreview?: (att: MessageAttachmentDto) => void;
  onRemove?: (att: MessageAttachmentDto) => void;
}) {
  if (images.length === 0 && files.length === 0 && videos.length === 0) return null;
  const removable = (att: MessageAttachmentDto, key: string, node: ReactNode) =>
    onRemove ? (
      <span key={key} className="message-bubble-attachment-item">
        {node}
        <button
          type="button"
          className="message-bubble-attachment-remove"
          title={`Remove ${att.name}`}
          aria-label={`Remove ${att.name}`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove(att);
          }}
        >
          <Icons.Close />
        </button>
      </span>
    ) : (
      <Fragment key={key}>{node}</Fragment>
    );
  return (
    <div className="message-bubble-attachments">
      {images.map((att) => {
        const src = attachmentSrc(att);
        if (!src) return null;
        return removable(att, `img-${att.name}-${src.slice(0, 24)}`,
          <button
            type="button"
            className="message-bubble-attachment-btn"
            onClick={
              onPreview
                ? (e) => {
                    e.stopPropagation();
                    onPreview(att);
                  }
                : undefined
            }
            title={onPreview ? `Open ${att.name}` : att.name}
          >
            <img src={src} alt={att.name} loading="lazy" className="message-bubble-attachment-image" />
          </button>,
        );
      })}
      {videos.map((att) => {
        // Same small thumb as a picture; the player and the frames open in the preview.
        const poster = att.frames?.find((f) => f.media_url)?.media_url;
        if (!poster && !att.media_url) {
          return removable(att, `vid-${att.name}`,
            <span className="message-bubble-attachment-file">
              <span className="message-bubble-attachment-file-icon">
                <Icons.Play />
              </span>
              <span className="message-bubble-attachment-file-name">{att.name}</span>
            </span>,
          );
        }
        return removable(att, `vid-${att.name}-${att.media_url ?? ""}`,
          <button
            type="button"
            className="message-bubble-attachment-btn message-bubble-attachment-video-btn"
            onClick={
              onPreview
                ? (e) => {
                    e.stopPropagation();
                    onPreview(att);
                  }
                : undefined
            }
            title={onPreview ? `Play ${att.name}` : att.name}
          >
            {poster ? (
              <img src={poster} alt={att.name} className="message-bubble-attachment-image" />
            ) : (
              <video src={att.media_url} muted preload="metadata" className="message-bubble-attachment-image" />
            )}
            <span className="message-bubble-attachment-video-badge" aria-hidden>
              <Icons.Play />
            </span>
          </button>,
        );
      })}
      {files.map((att) => removable(att, `file-${att.name}`,
        <button
          type="button"
          className="message-bubble-attachment-file"
          onClick={
            onPreview
              ? (e) => {
                  e.stopPropagation();
                  onPreview(att);
                }
              : undefined
          }
          title={onPreview ? `Open ${att.name}` : att.name}
        >
          <span className="message-bubble-attachment-file-icon">
            <Icons.File />
          </span>
          <span className="message-bubble-attachment-file-name">{att.name}</span>
        </button>,
      ))}
    </div>
  );
}

export const EditableUserMessage = memo(function EditableUserMessage({
  text,
  attachments,
  editable,
  currentMode,
  currentModel,
  codingAgent,
  setCodingAgent,
  convId,
  onResend,
  onStop,
  onRestart,
}: EditableUserMessageProps) {
  const items = attachments ?? [];
  const images = items.filter((a) => a.kind === "image");
  const files = items.filter((a) => a.kind === "file");
  const videos = items.filter((a) => a.kind === "video");
  /** What the edit keeps: the ✕ on a thumbnail drops it from the resend. */
  const [editItems, setEditItems] = useState<MessageAttachmentDto[]>(items);

  const [preview, setPreview] = useState<MessageAttachmentDto | null>(null);
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [faded, setFaded] = useState(false);
  const [draft, setDraft] = useState(text);
  const [editMode, setEditMode] = useState<AgentMode>(currentMode);
  const [editModel, setEditModel] = useState(currentModel);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // Grow the textarea to fit its content (up to a cap), replacing the old drag handle.
  const autoSize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(MIN_EDIT_HEIGHT, Math.min(el.scrollHeight, MAX_EDIT_HEIGHT))}px`;
  }, []);

  // Keep the draft in sync with the underlying message when not editing (e.g. the
  // row's text changes after a reload/reconcile).
  useEffect(() => {
    if (!editing) setDraft(text);
  }, [text, editing]);

  // Fade only when the clamped box actually overflows (short prompts stay sharp).
  useLayoutEffect(() => {
    if (expanded || editing) {
      setFaded(false);
      return;
    }
    const el = textRef.current;
    if (!el) {
      setFaded(false);
      return;
    }
    setFaded(el.scrollHeight > el.clientHeight + 1);
  }, [text, expanded, editing, images.length, videos.length]);

  const beginEdit = () => {
    if (!editable) return;
    setDraft(text);
    setEditItems(items);
    setEditMode(currentMode);
    setEditModel(currentModel);
    setEditing(true);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
        autoSize();
      }
    });
  };

  const cancelEdit = useCallback(() => {
    setEditing(false);
    setDraft(text);
  }, [text]);

  const toggleExpanded = () => setExpanded((v) => !v);

  const onBubbleActivate = () => {
    if (editable) beginEdit();
    else toggleExpanded();
  };

  // While editing, Escape anywhere in the app cancels, and a pointer press outside
  // the edit box cancels too — but not clicks inside a portaled model/mode dropdown.
  useEffect(() => {
    if (!editing) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        cancelEdit();
      }
    };
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target) return;
      if (boxRef.current?.contains(target)) return;
      if (target.closest(".dropdown-panel")) return;
      cancelEdit();
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [editing, cancelEdit]);

  const submit = () => {
    const trimmed = draft.trim();
    if (!trimmed && editItems.length === 0) return;
    setEditing(false);
    onResend(trimmed, editMode, editModel, editItems.length ? editItems : undefined);
  };

  if (editing) {
    return (
      <div className="message-bubble-user-wrap">
        <div className="message-bubble-user-row">
          <div className="msg-edit-box" ref={boxRef}>
            <AttachmentStrip
              images={editItems.filter((a) => a.kind === "image")}
              files={editItems.filter((a) => a.kind === "file")}
              videos={editItems.filter((a) => a.kind === "video")}
              onRemove={(att) => setEditItems((prev) => prev.filter((a) => a !== att))}
            />
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                autoSize();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              className="msg-edit-textarea"
              placeholder="Edit and resend… (Shift+Enter for newline)"
            />
            <div className="msg-edit-toolbar">
              <div className="msg-edit-toolbar-left">
                <ModeSelector activeMode={editMode} setMode={setEditMode} />
                <ModelSelector
                  selectedModel={editModel}
                  setSelectedModel={setEditModel}
                  codingAgent={codingAgent}
                  setCodingAgent={setCodingAgent}
                  convId={convId}
                  preserveSelection
                />
              </div>
              <div className="msg-edit-toolbar-right">
                {onStop ? <InlineStopButton onClick={onStop} /> : null}
                <button type="button" className="msg-edit-cancel-btn" onClick={cancelEdit}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="msg-edit-send-btn"
                  onClick={submit}
                  disabled={!draft.trim() && editItems.length === 0}
                  title="Resend"
                  aria-label="Resend"
                >
                  <Icons.Send />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const interactive = Boolean(editable || text || items.length > 0);
  const hasMedia = images.length > 0 || videos.length > 0;
  const contentClass = [
    "message-bubble-user-content",
    editable ? "message-bubble-user-content--editable" : "",
    !editable && text ? "message-bubble-user-content--expandable" : "",
    hasMedia ? "message-bubble-user-content--with-media" : "",
    expanded ? "is-expanded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="message-bubble-user-wrap">
      <div className="message-bubble-user-row">
        <div
          className={contentClass}
          onClick={
            interactive
              ? (event) => {
                  if ((event.target as HTMLElement).closest?.(".chat-ref-chip")) return;
                  onBubbleActivate();
                }
              : undefined
          }
          role={interactive ? "button" : undefined}
          tabIndex={interactive ? 0 : undefined}
          aria-expanded={!editable && text ? expanded : undefined}
          onKeyDown={
            interactive
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onBubbleActivate();
                  }
                }
              : undefined
          }
          title={editable ? "Edit and resend" : expanded ? "Collapse" : "Expand"}
        >
          {editable || onStop || text || onRestart ? (
            <div className="message-bubble-user-actions">
              {onStop ? <InlineStopButton onClick={onStop} /> : null}
              {text ? <PromptCopyButton text={text} /> : null}
              {onRestart ? (
                <button
                  type="button"
                  className="message-bubble-user-edit-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRestart();
                  }}
                  title="Restart from here: back in the box to edit, this and everything after it erased"
                  aria-label="Restart from here"
                >
                  <Icons.Replay />
                </button>
              ) : null}
              {editable ? (
                <button
                  type="button"
                  className="message-bubble-user-edit-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    beginEdit();
                  }}
                  title="Edit and resend"
                  aria-label="Edit and resend"
                >
                  <Icons.Pencil />
                </button>
              ) : null}
            </div>
          ) : null}
          <AttachmentStrip images={images} files={files} videos={videos} onPreview={setPreview} />
          {text ? (
            <div
              ref={textRef}
              className={`message-bubble-user-text${faded ? " is-faded" : ""}`}
            >
              <ChatRefText text={text} />
            </div>
          ) : null}
        </div>
      </div>
      <AttachmentPreviewModal open={preview !== null} attachment={preview} onClose={() => setPreview(null)} />
    </div>
  );
});

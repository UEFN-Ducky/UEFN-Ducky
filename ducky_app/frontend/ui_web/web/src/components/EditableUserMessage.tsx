import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AgentMode, MessageAttachmentDto } from "../types/panel";
import { Icons } from "../icons/Icons";
import { AttachmentPreviewModal } from "./AttachmentPreviewModal";
import { ModeSelector } from "./ModeSelector";
import { ModelSelector } from "./ModelSelector";
import { InlineStopButton } from "./InlineStopButton";

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
}

function attachmentSrc(att: MessageAttachmentDto): string | null {
  if (att.kind !== "image" || !att.data_base64) return null;
  const mime = att.mime || "image/png";
  return `data:${mime};base64,${att.data_base64}`;
}

/** Read-only thumbnails/chips for the message's attachments. */
function AttachmentStrip({
  images,
  files,
  onPreview,
}: {
  images: MessageAttachmentDto[];
  files: MessageAttachmentDto[];
  onPreview?: (att: MessageAttachmentDto) => void;
}) {
  if (images.length === 0 && files.length === 0) return null;
  return (
    <div className="message-bubble-attachments">
      {images.map((att) => {
        const src = attachmentSrc(att);
        if (!src) return null;
        return (
          <button
            key={`img-${att.name}-${src.slice(0, 24)}`}
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
            <img src={src} alt={att.name} className="message-bubble-attachment-image" />
          </button>
        );
      })}
      {files.map((att) => (
        <button
          key={`file-${att.name}`}
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
        </button>
      ))}
    </div>
  );
}

export function EditableUserMessage({
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
}: EditableUserMessageProps) {
  const items = attachments ?? [];
  const images = items.filter((a) => a.kind === "image");
  const files = items.filter((a) => a.kind === "file");

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
  }, [text, expanded, editing, images.length]);

  const beginEdit = () => {
    if (!editable) return;
    setDraft(text);
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
    if (!trimmed && items.length === 0) return;
    setEditing(false);
    onResend(trimmed, editMode, editModel, items.length ? items : undefined);
  };

  if (editing) {
    return (
      <div className="message-bubble-user-wrap">
        <div className="message-bubble-user-row">
          <div className="msg-edit-box" ref={boxRef}>
            <AttachmentStrip images={images} files={files} />
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
                <div className="chat-pane-toolbar-divider" />
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
                  disabled={!draft.trim() && items.length === 0}
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
  const hasImages = images.length > 0;
  const contentClass = [
    "message-bubble-user-content",
    editable ? "message-bubble-user-content--editable" : "",
    !editable && text ? "message-bubble-user-content--expandable" : "",
    hasImages ? "message-bubble-user-content--with-media" : "",
    expanded ? "is-expanded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="message-bubble-user-wrap">
      <div className="message-bubble-user-row">
        <div
          className={contentClass}
          onClick={interactive ? onBubbleActivate : undefined}
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
          {editable || onStop ? (
            <div className="message-bubble-user-actions">
              {onStop ? <InlineStopButton onClick={onStop} /> : null}
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
          <AttachmentStrip images={images} files={files} onPreview={setPreview} />
          {text ? (
            <div
              ref={textRef}
              className={`message-bubble-user-text${faded ? " is-faded" : ""}`}
            >
              {text}
            </div>
          ) : null}
        </div>
      </div>
      <AttachmentPreviewModal open={preview !== null} attachment={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

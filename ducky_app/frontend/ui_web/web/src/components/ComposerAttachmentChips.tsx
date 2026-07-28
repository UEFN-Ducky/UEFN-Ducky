import type { ComposerAttachment } from "../types/panel";
import { Icons } from "../icons/Icons";

interface ComposerAttachmentChipsProps {
  attachments: ComposerAttachment[];
  onRemove: (id: string) => void;
  /** Open a full-size preview (with drawing tools for images). */
  onPreview?: (att: ComposerAttachment) => void;
}

export function ComposerAttachmentChips({ attachments, onRemove, onPreview }: ComposerAttachmentChipsProps) {
  if (attachments.length === 0) return null;

  return (
    <div className="composer-attachment-chips">
      {attachments.map((att) => (
        <div key={att.id} className={`composer-attachment-chip composer-attachment-chip--${att.kind}`}>
          <button
            type="button"
            className="composer-attachment-chip-open"
            aria-label={`Preview ${att.name}`}
            title={att.kind === "image" ? "Click to preview / draw" : "Click to preview"}
            onClick={() => onPreview?.(att)}
            disabled={!onPreview}
          >
            {att.kind === "image" ? (
              <img src={att.dataUrl} alt={att.name} className="composer-attachment-chip-thumb" />
            ) : (
              <span className="composer-attachment-chip-file-icon" aria-hidden>
                <Icons.File />
              </span>
            )}
            <span className="composer-attachment-chip-name" title={att.name}>
              {att.name}
            </span>
          </button>
          <button
            type="button"
            className="composer-attachment-chip-remove"
            aria-label={`Remove ${att.name}`}
            onClick={() => onRemove(att.id)}
          >
            <Icons.Close />
          </button>
        </div>
      ))}
    </div>
  );
}

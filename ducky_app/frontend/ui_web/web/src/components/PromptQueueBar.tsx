import { Icons } from "../icons/Icons";
import type { QueuedPrompt } from "../hooks/promptQueue";

interface PromptQueueBarProps {
  items: QueuedPrompt[];
  /** Remove this prompt from the queue and restore it to the main composer. */
  onEdit: (id: string) => void;
  /** Stop the live turn (if any) and run this prompt next. */
  onSendNow: (id: string) => void;
  onDelete: (id: string) => void;
  /** The prompt Send now already handed to the running agent: it goes in at the agent's
   *  next step, and pressing its button again stops the current step and sends it at once. */
  steeringId?: string | null;
}

export function PromptQueueBar({ items, onEdit, onSendNow, onDelete, steeringId = null }: PromptQueueBarProps) {
  if (items.length === 0) return null;

  return (
    <div className="prompt-queue" aria-label="Queued follow-ups">
      <div className="prompt-queue-header">
        {items.length} Queued
      </div>
      <ul className="prompt-queue-list">
        {items.map((item) => (
          <li key={item.id} className="prompt-queue-item">
            <div
              className="prompt-queue-text"
              title={item.text || item.attachments.map((a) => a.name).join(", ") || undefined}
            >
              {item.text ||
                (item.attachments.length === 1
                  ? item.attachments[0].name
                  : item.attachments.length
                    ? `${item.attachments.length} files`
                    : "")}
            </div>
            {item.attachments.length > 0 ? (
              <div className="prompt-queue-meta">
                {item.attachments.length} file{item.attachments.length === 1 ? "" : "s"}
              </div>
            ) : null}
            {item.id === steeringId ? (
              <div className="prompt-queue-meta prompt-queue-meta--steering" role="status">
                Goes in at the agent's next step
              </div>
            ) : null}
            <div className="prompt-queue-actions">
              <button
                type="button"
                className="prompt-queue-icon-btn"
                title="Edit in composer (removes from queue)"
                aria-label="Edit queued prompt"
                onClick={() => onEdit(item.id)}
              >
                <Icons.Pencil />
              </button>
              <button
                type="button"
                className={`prompt-queue-icon-btn${item.id === steeringId ? " is-armed" : ""}`}
                title={item.id === steeringId
                  ? "Stop the current step and send this now"
                  : item.attachments.length
                    ? "Send now (stops current turn)"
                    : "Send now (the agent reads it at its next step)"}
                aria-label={item.id === steeringId ? "Stop the current step and send this now" : "Send queued prompt now"}
                onClick={() => onSendNow(item.id)}
              >
                <span className="prompt-queue-up-icon" aria-hidden>
                  ↑
                </span>
              </button>
              <button
                type="button"
                className="prompt-queue-icon-btn"
                title="Delete"
                aria-label="Delete queued prompt"
                onClick={() => onDelete(item.id)}
              >
                <Icons.Trash />
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

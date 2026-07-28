import { useState } from "react";
import { Icons } from "../icons/Icons";
import type { QueuedPrompt } from "../hooks/promptQueue";

interface PromptQueueBarProps {
  items: QueuedPrompt[];
  onEdit: (id: string, text: string) => void;
  onMoveToFront: (id: string) => void;
  onDelete: (id: string) => void;
}

export function PromptQueueBar({ items, onEdit, onMoveToFront, onDelete }: PromptQueueBarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  if (items.length === 0) return null;

  const startEdit = (item: QueuedPrompt) => {
    setEditingId(item.id);
    setDraft(item.text);
  };

  const commitEdit = () => {
    if (!editingId) return;
    onEdit(editingId, draft);
    setEditingId(null);
    setDraft("");
  };

  return (
    <div className="prompt-queue" aria-label="Queued follow-ups">
      <div className="prompt-queue-header">
        {items.length} Queued
      </div>
      <ul className="prompt-queue-list">
        {items.map((item) => (
          <li key={item.id} className="prompt-queue-item">
            {editingId === item.id ? (
              <div className="prompt-queue-edit">
                <textarea
                  className="prompt-queue-edit-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      commitEdit();
                    }
                    if (e.key === "Escape") {
                      setEditingId(null);
                      setDraft("");
                    }
                  }}
                  autoFocus
                  rows={2}
                />
                <div className="prompt-queue-edit-actions">
                  <button type="button" className="prompt-queue-btn" onClick={commitEdit}>
                    Save
                  </button>
                  <button
                    type="button"
                    className="prompt-queue-btn"
                    onClick={() => {
                      setEditingId(null);
                      setDraft("");
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
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
                <div className="prompt-queue-actions">
                  <button
                    type="button"
                    className="prompt-queue-icon-btn"
                    title="Edit"
                    aria-label="Edit queued prompt"
                    onClick={() => startEdit(item)}
                  >
                    <Icons.Pencil />
                  </button>
                  <button
                    type="button"
                    className="prompt-queue-icon-btn"
                    title="Move to front"
                    aria-label="Move queued prompt to front"
                    disabled={items[0]?.id === item.id}
                    onClick={() => onMoveToFront(item.id)}
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
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

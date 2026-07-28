import { useEffect, useRef, useState } from "react";

import type { FileHistoryEntry } from "../types/panel";
import { formatSavedAt } from "../utils/formatSavedAt";
import {
  getHistoryHoverPreview,
  setHistoryHoverPreview,
} from "../verse-editor/history/historyPreviewSettings";
import { TruncatedText } from "./TruncatedText";

interface FileEditHistoryContentProps {
  entries: FileHistoryEntry[];
  loading: boolean;
  totalEntryCount?: number;
  previewEntryId?: string | null;
  activeEntryId?: string | null;
  /** First line of the live editor buffer. When set, a pinned "Current" row is always shown. */
  currentPreview?: string | null;
  /** True when the live buffer has edits not yet written to disk. */
  currentIsUnsaved?: boolean;
  onPreview: (entry: FileHistoryEntry) => void;
  onClearPreview: () => void;
  onRestore: (entry: FileHistoryEntry) => void;
  variant?: "dropdown" | "panel";
}

const CLICK_DELAY_MS = 250;

export function FileEditHistoryContent({
  entries,
  loading,
  totalEntryCount,
  previewEntryId,
  activeEntryId,
  currentPreview,
  currentIsUnsaved = false,
  onPreview,
  onClearPreview,
  onRestore,
  variant = "panel",
}: FileEditHistoryContentProps) {
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewEntry = previewEntryId ? entries.find((e) => e.id === previewEntryId) : null;
  const [hoverPreview, setHoverPreview] = useState(getHistoryHoverPreview);

  useEffect(
    () => () => {
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
      if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    },
    [],
  );

  const schedulePreview = (entry: FileHistoryEntry) => {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => onPreview(entry), 120);
  };

  const cancelScheduledPreview = () => {
    if (previewTimerRef.current) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
  };

  const toggleHoverPreview = (next: boolean) => {
    setHoverPreview(next);
    setHistoryHoverPreview(next);
    // Turning hover off mid-hover must not leave a scheduled preview to fire.
    if (!next) cancelScheduledPreview();
  };

  const hoverPreviewToggle = (
    <label
      className={`file-history-hover-toggle ${variant === "dropdown" ? "file-history-header" : "file-history-panel-hint"}`}
      title="Preview a version when you hover it"
    >
      <input
        type="checkbox"
        checked={hoverPreview}
        onChange={(e) => toggleHoverPreview(e.target.checked)}
      />
      Hover preview
    </label>
  );

  const handleClick = (entry: FileHistoryEntry) => {
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    clickTimerRef.current = setTimeout(() => {
      clickTimerRef.current = null;
      onPreview(entry);
    }, CLICK_DELAY_MS);
  };

  const handleDoubleClick = (entry: FileHistoryEntry, e: React.MouseEvent) => {
    e.preventDefault();
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
    cancelScheduledPreview();
    onClearPreview();
    onRestore(entry);
  };

  return (
    <div className={`file-history-content${variant === "panel" ? " file-history-panel" : ""}`}>
      {hoverPreviewToggle}

      <div className="file-history-list">
        {/* Always-present anchor: the live editor buffer. Never disappears (unlike the old
            per-entry "Current" badge, which vanished the moment your edits matched no saved
            version). Click it to leave a preview and return to your live content. */}
        {currentPreview != null ? (
          <button
            type="button"
            className={`file-history-item file-history-item--current${previewEntryId ? "" : " is-active"}`}
            title="Your current editor content"
            onClick={() => onClearPreview()}
            onDoubleClick={(e) => e.preventDefault()}
          >
            <span className="file-history-item-head">
              <span className="file-history-item-time">Current</span>
              <span
                className={`file-history-item-badge${currentIsUnsaved ? " file-history-item-badge--unsaved" : ""}`}
              >
                {currentIsUnsaved ? "Unsaved" : "Saved"}
              </span>
            </span>
            {currentPreview ? (
              <TruncatedText className="file-history-item-preview" title={currentPreview}>
                {currentPreview}
              </TruncatedText>
            ) : (
              <span className="file-history-item-preview">(empty file)</span>
            )}
          </button>
        ) : null}

        {loading ? (
          <div className="file-history-empty">Loading history…</div>
        ) : entries.length === 0 ? (
          <div className="file-history-empty">
            {totalEntryCount && totalEntryCount > 0
              ? "No versions in this date range."
              : "No saved versions yet — history starts after your first save or AI edit."}
          </div>
        ) : (
          entries.map((entry) => {
            const isPreviewing = previewEntryId === entry.id;
            const isActive = activeEntryId === entry.id && !isPreviewing;
            const isAgent = entry.source === "agent";
            return (
              <button
                key={entry.id}
                type="button"
                className={`file-history-item${isPreviewing ? " is-previewing" : ""}${isActive ? " is-active" : ""}`}
                onMouseEnter={() => hoverPreview && schedulePreview(entry)}
                onMouseLeave={cancelScheduledPreview}
                onFocus={() => hoverPreview && schedulePreview(entry)}
                onClick={() => handleClick(entry)}
                onDoubleClick={(e) => handleDoubleClick(entry, e)}
              >
                <span className="file-history-item-head">
                  <span className="file-history-item-time">{formatSavedAt(entry.saved_at)}</span>
                  {isAgent ? (
                    <span className="file-history-item-badge file-history-item-badge--agent" title="Written by AI">
                      AI
                    </span>
                  ) : null}
                  {/* "Current" is owned by the pinned row above; here we only tint the
                      matching saved version so you can see which snapshot equals your buffer. */}
                  {isActive ? (
                    <span className="file-history-item-badge">Matches current</span>
                  ) : null}
                </span>
                {entry.preview ? (
                  <TruncatedText className="file-history-item-preview" title={entry.preview}>
                    {entry.preview}
                  </TruncatedText>
                ) : null}
              </button>
            );
          })
        )}
      </div>

      {previewEntry ? (
        <div className="file-history-footer">
          <span className="file-history-footer-hint">
            Previewing {formatSavedAt(previewEntry.saved_at)} — your edits are kept
          </span>
          <div className="file-history-footer-actions">
            <button type="button" className="file-history-footer-btn" onClick={() => onClearPreview()}>
              Dismiss
            </button>
            <button
              type="button"
              className="file-history-footer-btn is-primary"
              onClick={() => onRestore(previewEntry)}
            >
              Restore
            </button>
          </div>
        </div>
      ) : (
        <div className="file-history-footer file-history-footer-muted">
          Double-click a version to restore (your current edits are saved first)
        </div>
      )}
    </div>
  );
}

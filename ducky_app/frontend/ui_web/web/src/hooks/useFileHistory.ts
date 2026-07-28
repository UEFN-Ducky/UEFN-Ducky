import { useCallback, useEffect, useRef, useState } from "react";

import { getApi } from "./usePanelApi";
import type { FileHistoryEntry } from "../types/panel";
import { useVerseEditor } from "../verse-editor";
import { isVerseFile, normPath } from "../verse-editor/utils/isVerseFile";
import { formatSavedAt } from "../utils/formatSavedAt";
import { contentHashPrefix } from "../utils/contentHash";

export function useFileHistory(
  filePath: string,
  options?: { enabled?: boolean; refreshKey?: number; contentVersion?: number },
) {
  const {
    historyPreview,
    previewHistoryEntry,
    clearHistoryPreview,
    restoreFromHistory,
    getEditorContent,
    isPathDirty,
  } = useVerseEditor();
  const enabled = options?.enabled ?? true;
  const refreshKey = options?.refreshKey ?? 0;
  const contentVersion = options?.contentVersion ?? 0;

  const [entries, setEntries] = useState<FileHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);
  // First meaningful line of the LIVE editor buffer, so the panel can always show a
  // "Current" row — even with unsaved edits that match no saved snapshot.
  const [currentPreview, setCurrentPreview] = useState<string | null>(null);

  // Preview fetches are async; bumping this token invalidates any in-flight
  // fetch so a preview can't apply after it was dismissed (stuck decorations).
  const previewSeqRef = useRef(0);

  const loadHistory = useCallback(async () => {
    if (!filePath || !isVerseFile(filePath)) {
      setEntries([]);
      return;
    }

    const api = getApi();
    if (!api) return;

    setLoading(true);
    try {
      const next = await api.list_file_history(filePath);
      setEntries(next);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [filePath]);

  useEffect(() => {
    if (!enabled) return;
    void loadHistory();
  }, [enabled, loadHistory, refreshKey]);

  useEffect(() => {
    if (!enabled || !filePath) {
      setActiveEntryId(null);
      setCurrentPreview(null);
      return;
    }

    const editorText = getEditorContent(filePath);
    if (editorText == null) {
      // No editor mounted for this file — nothing live to show as "Current".
      setActiveEntryId(null);
      setCurrentPreview(null);
      return;
    }

    // Always surface the live buffer as "Current", even before the first save and
    // even when the content matches no saved snapshot.
    setCurrentPreview(firstMeaningfulLine(editorText));

    if (!entries.length) {
      setActiveEntryId(null);
      return;
    }

    let cancelled = false;
    void contentHashPrefix(editorText).then((hash) => {
      if (cancelled) return;
      const match = entries.find((entry) => entry.content_hash && entry.content_hash === hash);
      setActiveEntryId(match?.id ?? null);
    });

    return () => {
      cancelled = true;
    };
  }, [enabled, filePath, entries, contentVersion, getEditorContent]);

  const previewEntryId =
    historyPreview && normPath(historyPreview.path) === normPath(filePath)
      ? historyPreview.entryId
      : null;

  const preview = useCallback(
    async (entry: FileHistoryEntry) => {
      const api = getApi();
      if (!api) return;

      const seq = ++previewSeqRef.current;
      try {
        const result = await api.read_file_history_entry(filePath, entry.id);
        if (seq !== previewSeqRef.current) return; // dismissed or superseded while fetching
        previewHistoryEntry(
          filePath,
          entry.id,
          result.content,
          formatSavedAt(entry.saved_at),
        );
      } catch {
        // ignore
      }
    },
    [filePath, previewHistoryEntry],
  );

  const clearPreview = useCallback(() => {
    previewSeqRef.current += 1;
    clearHistoryPreview(filePath);
  }, [filePath, clearHistoryPreview]);

  // Dropdown/panel unmounting or switching files must not strand a live preview.
  useEffect(() => {
    if (!filePath) return;
    return () => {
      previewSeqRef.current += 1;
      clearHistoryPreview(filePath);
    };
  }, [filePath, clearHistoryPreview]);

  const restore = useCallback(
    async (entry: FileHistoryEntry) => {
      const api = getApi();
      if (!api) return;

      previewSeqRef.current += 1;
      try {
        const result = await api.read_file_history_entry(filePath, entry.id);
        const currentContent = getEditorContent(filePath);
        if (currentContent != null && currentContent !== result.content) {
          const currentHash = await contentHashPrefix(currentContent);
          const targetHash = entry.content_hash || (await contentHashPrefix(result.content));
          const newestHash = entries[0]?.content_hash;
          if (currentHash !== targetHash && currentHash !== newestHash) {
            await api.snapshot_file_history(filePath, currentContent);
          }
        }

        const restored = restoreFromHistory(filePath, result.content, entry.id);
        if (!restored) return;

        clearHistoryPreview(filePath);
        setActiveEntryId(entry.id);
        await loadHistory();
      } catch {
        // ignore
      }
    },
    [filePath, restoreFromHistory, getEditorContent, entries, clearHistoryPreview, loadHistory],
  );

  return {
    entries,
    loading,
    previewEntryId,
    activeEntryId,
    currentPreview,
    currentIsUnsaved: filePath ? isPathDirty(filePath) : false,
    preview,
    clearPreview,
    restore,
    refresh: loadHistory,
  };
}

/** First non-blank line of the buffer — the stable identity line shown on the Current row. */
function firstMeaningfulLine(text: string): string {
  for (const line of text.split(/\r\n|\r|\n/)) {
    const trimmed = line.trim();
    if (trimmed) return trimmed;
  }
  return "";
}

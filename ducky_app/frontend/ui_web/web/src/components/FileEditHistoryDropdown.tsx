import { useEffect, useRef, useState } from "react";

import { Icons } from "../icons/Icons";
import type { FileHistoryEntry } from "../types/panel";
import { FileEditHistoryContent } from "./FileEditHistoryContent";

interface FileEditHistoryDropdownProps {
  filePath: string;
  entries: FileHistoryEntry[];
  loading: boolean;
  previewEntryId?: string | null;
  activeEntryId?: string | null;
  onPreview: (entry: FileHistoryEntry) => void;
  onClearPreview: () => void;
  onRestore: (entry: FileHistoryEntry) => void;
  onOpenChange?: (open: boolean) => void;
}

export function FileEditHistoryDropdown({
  filePath: _filePath,
  entries,
  loading,
  previewEntryId,
  activeEntryId,
  onPreview,
  onClearPreview,
  onRestore,
  onOpenChange,
}: FileEditHistoryDropdownProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const setOpenState = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
    if (!next) onClearPreview();
  };

  useEffect(() => {
    if (!open) return;

    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpenState(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenState(false);
    };

    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClearPreview]);

  return (
    <div className="file-history-root" ref={rootRef}>
      <button
        type="button"
        className={`icon-btn file-history-trigger ${open ? "is-active" : ""}`}
        title="File history"
        aria-label="File history"
        onClick={() => setOpenState(!open)}
      >
        <Icons.Clock />
      </button>

      {open ? (
        <div className="file-history-menu no-drag">
          <FileEditHistoryContent
            variant="dropdown"
            entries={entries}
            loading={loading}
            previewEntryId={previewEntryId}
            activeEntryId={activeEntryId}
            onPreview={onPreview}
            onClearPreview={onClearPreview}
            onRestore={(entry) => {
              onRestore(entry);
              setOpenState(false);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

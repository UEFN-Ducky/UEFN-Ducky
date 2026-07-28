import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { EditorDropZone } from "../types/panel";
import { dropZoneFromPointer } from "../utils/editorLayoutOps";
import {
  getEditorTabDragData,
  getEditorTabGroupDragData,
  handleExternalTabDrop,
  isEditorTabDrag,
  markEditorTabDropped,
} from "../utils/editorTabDrag";
import { dragHasOsFiles, isChatAttachDropTarget, OPEN_EXTERNAL_TARGET } from "../utils/osFileDrag";
import { getApi } from "../hooks/usePanelApi";
import {
  getSidebarEditorDropPreview,
  subscribeSidebarEditorDropPreview,
} from "../utils/sidebarDragOut";

interface EditorTabDropSurfaceProps {
  children: ReactNode;
  targetGroupId: string;
  onDropTab: (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => void;
  className?: string;
}

/** Accepts editor-tab drags (including cross-window) when no editor group pane is
 * mounted, and OS/Explorer file drags — arming the "Open file" target so a drop on the
 * empty state opens the file(s) in place, just like a drop on the editor body. */
export function EditorTabDropSurface({
  children,
  targetGroupId,
  onDropTab,
  className = "",
}: EditorTabDropSurfaceProps) {
  const [dropZone, setDropZone] = useState<EditorDropZone | null>(null);
  // An OS file is being dragged over the empty state → show the "Open file" overlay.
  const [externalOver, setExternalOver] = useState(false);
  const externalArmedRef = useRef(false);
  const sidebarPreview = useSyncExternalStore(
    subscribeSidebarEditorDropPreview,
    getSidebarEditorDropPreview,
    () => null,
  );
  const sidebarZone =
    sidebarPreview?.groupId === targetGroupId ? sidebarPreview.zone : null;
  const visibleDropZone = dropZone ?? sidebarZone;

  // Report the sentinel once per drag-enter so the native drop handler opens the file(s)
  // instead of bailing out on an empty drop target.
  const armExternalDrop = useCallback(() => {
    if (externalArmedRef.current) return;
    externalArmedRef.current = true;
    setExternalOver(true);
    getApi()?.set_import_drop_target?.(OPEN_EXTERNAL_TARGET)?.catch?.(() => {});
  }, []);

  const disarmExternalDrop = useCallback((clearTarget: boolean) => {
    if (!externalArmedRef.current) return;
    externalArmedRef.current = false;
    setExternalOver(false);
    // On drop, leave the target for the native handler to consume; only clear it when
    // the drag leaves without dropping.
    if (clearTarget) getApi()?.set_import_drop_target?.("")?.catch?.(() => {});
  }, []);

  // Same stuck-overlay path as EditorGroupPane: cancel / drop-on-tab-strip skips leave+drop.
  useEffect(() => {
    const clear = () => {
      setDropZone(null);
      disarmExternalDrop(true);
    };
    document.addEventListener("dragend", clear);
    return () => document.removeEventListener("dragend", clear);
  }, [disarmExternalDrop]);

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      if (dragHasOsFiles(e.dataTransfer)) {
        // Chat pane owns this drag as an attachment upload — hide "Open file" but do
        // not clear the import target (chat sets CHAT_ATTACH_TARGET for the native drop).
        if (isChatAttachDropTarget(e.target)) {
          e.preventDefault();
          if (externalArmedRef.current) {
            externalArmedRef.current = false;
            setExternalOver(false);
          }
          return;
        }
        // OS file → arm "Open file". preventDefault keeps the drop allowed; never
        // stopPropagation (the drop must reach the native document handler).
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        armExternalDrop();
        return;
      }
      if (!isEditorTabDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const rect = e.currentTarget.getBoundingClientRect();
      setDropZone(dropZoneFromPointer(rect, e.clientX, e.clientY));
    },
    [armExternalDrop],
  );

  const handleDragLeave = useCallback(
    (e: React.DragEvent) => {
      if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
        setDropZone(null);
        disarmExternalDrop(true);
      }
    },
    [disarmExternalDrop],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      if (dragHasOsFiles(e.dataTransfer)) {
        // Let it bubble to pywebview's native document drop handler, which reads the OS
        // path and fires ducky:external-files-open. Just drop the overlay.
        disarmExternalDrop(false);
        return;
      }
      if (!isEditorTabDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      if (handleExternalTabDrop(e)) {
        setDropZone(null);
        return;
      }
      markEditorTabDropped();
      const tabId = getEditorTabDragData(e.dataTransfer);
      const sourceGroupId = getEditorTabGroupDragData(e.dataTransfer);
      const rect = e.currentTarget.getBoundingClientRect();
      const zone = dropZone ?? dropZoneFromPointer(rect, e.clientX, e.clientY);
      setDropZone(null);
      if (!tabId || !sourceGroupId) return;
      onDropTab(targetGroupId, tabId, sourceGroupId, zone);
    },
    [dropZone, onDropTab, targetGroupId, disarmExternalDrop],
  );

  return (
    <div
      className={`editor-tab-drop-surface${className ? ` ${className}` : ""}`}
      data-editor-group-id={targetGroupId}
      onDragOverCapture={handleDragOver}
      onDragLeave={handleDragLeave}
      onDropCapture={handleDrop}
    >
      {visibleDropZone ? (
        <div className={`editor-drop-overlay editor-drop-overlay--${visibleDropZone}`} />
      ) : null}
      {externalOver ? (
        <div className="editor-drop-overlay editor-drop-overlay--center editor-drop-overlay--external">
          Open file
        </div>
      ) : null}
      {children}
    </div>
  );
}

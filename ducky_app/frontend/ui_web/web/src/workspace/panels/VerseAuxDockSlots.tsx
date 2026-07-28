import { memo } from "react";
import { useVerseEditorOptional } from "../../verse-editor/VerseEditorProvider";
import { VerseHistoryBody, VerseOutlineBody } from "./VerseAuxPanel";

/** Stable outline mount — bridge lookup stays inside so parent dock layout does not remount on every render. */
export const VerseOutlineSlot = memo(function VerseOutlineSlot({
  versePath,
  enabled,
}: {
  versePath?: string;
  enabled: boolean;
}) {
  const verseEditor = useVerseEditorOptional();
  const bridge = versePath && verseEditor ? verseEditor.getEditorBridge(versePath) : null;

  return (
    // No sidebar-panel-scroll here — the virtualized outline owns its own scroll
    // element (it needs the scrollTop to window rows).
    <div className="verse-outline-body">
      <VerseOutlineBody
        filePath={versePath}
        model={bridge?.model ?? null}
        editorInstance={bridge?.editor ?? null}
        lspReady={bridge?.lspReady ?? false}
        enabled={enabled}
      />
    </div>
  );
});

export const VerseHistorySlot = memo(function VerseHistorySlot({
  versePath,
  enabled,
  historyRefreshKey,
}: {
  versePath?: string;
  enabled: boolean;
  historyRefreshKey: number;
}) {
  return (
    <div className="verse-outline-body">
      <VerseHistoryBody filePath={versePath} enabled={enabled} historyRefreshKey={historyRefreshKey} />
    </div>
  );
});

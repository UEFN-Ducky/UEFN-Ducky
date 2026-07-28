interface VerseEditorToolbarProps {
  path: string;
  dirty: boolean;
  saving: boolean;
  autoSaved?: boolean;
  lspReady?: boolean;
  lspError?: string;
  onSave: () => void;
}

export function VerseEditorToolbar({
  path,
  dirty,
  saving,
  autoSaved = false,
  lspReady = false,
  lspError = "",
  onSave,
}: VerseEditorToolbarProps) {
  let status = "";
  if (saving) status = "Saving…";
  else if (dirty) status = "Unsaved";
  else if (autoSaved) status = "Saved";

  const lspLabel = lspError ? "LSP error" : lspReady ? "LSP connected" : "LSP starting";

  return (
    <div className="verse-editor-toolbar">
      <span className="verse-editor-toolbar-path" data-no-translate>
        {path}
        {dirty ? " •" : ""}
      </span>
      <span
        className={`verse-editor-lsp-status ${lspError ? "is-error" : lspReady ? "is-ready" : ""}`}
        title={lspError || "Verse language server"}
      >
        {lspLabel}
      </span>
      {status ? <span className={`verse-editor-save-status ${dirty ? "is-dirty" : ""}`}>{status}</span> : null}
      <button
        type="button"
        className="verse-editor-save-btn"
        onClick={onSave}
        disabled={saving || !dirty}
        title="Save (Ctrl+S)"
      >
        Save
      </button>
    </div>
  );
}

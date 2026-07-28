import { useCallback, useState } from "react";
import { fileKey, type PackWithFiles, type SkillFile } from "../model/types";
import { fileBasename } from "../utils/fileDisplay";
import { Icons } from "./icons";
import * as api from "../api/skillPackStudioApi";

interface FileEditorPaneProps {
  pack: PackWithFiles;
  file: SkillFile;
  dirty: boolean;
  busy: boolean;
  onPatchFile: (packId: string, fileId: string, patch: Partial<SkillFile>) => void;
  onDirtyChange: (key: string, dirty: boolean) => void;
  onReload: () => void;
  onStatus: (msg: string) => void;
}

type DetailTab = "content" | "details";

export function FileEditorPane({
  pack,
  file,
  dirty,
  busy,
  onPatchFile,
  onDirtyChange,
  onReload,
  onStatus,
}: FileEditorPaneProps) {
  const [tab, setTab] = useState<DetailTab>("content");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const key = fileKey(pack.id, file.id);
  const isCore = file.id === "core";

  const saveMeta = useCallback(
    async (next?: Partial<SkillFile>) => {
      const merged = { ...file, ...next };
      try {
        await api.saveFileMeta(pack.id, file.id, {
          label: merged.title,
          description: merged.description,
          default_enabled: merged.defaultEnabled,
          load_condition: merged.loadCondition || null,
        });
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [pack.id, file, onStatus],
  );

  const saveContent = useCallback(async () => {
    try {
      await api.saveFileContent(pack.id, file.id, file.content);
      onDirtyChange(key, false);
      onStatus(`Saved ${pack.id}/${file.file}`);
    } catch (e) {
      onStatus(e instanceof Error ? e.message : String(e));
    }
  }, [pack.id, file, key, onDirtyChange, onStatus]);

  const handleDelete = useCallback(async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      await api.deleteFile(pack.id, file.id);
      setConfirmDelete(false);
      onReload();
    } catch (e) {
      onStatus(e instanceof Error ? e.message : String(e));
    }
  }, [confirmDelete, pack.id, file.id, onReload, onStatus]);

  return (
    <main className="sps-file-main">
      <div className="sps-editor-header">
        <span className="sps-file-path">{fileBasename(file.file)}</span>
      </div>

      <div className="sps-tabs">
        <button
          type="button"
          className={tab === "content" ? "sps-tab is-active" : "sps-tab"}
          onClick={() => setTab("content")}
        >
          Content
        </button>
        <button
          type="button"
          className={tab === "details" ? "sps-tab is-active" : "sps-tab"}
          onClick={() => setTab("details")}
        >
          Details
        </button>
      </div>

      {tab === "content" ? (
        <div className="sps-content-pane">
          <textarea
            className="sps-textarea sps-textarea--editor"
            value={file.content}
            spellCheck={false}
            onChange={(e) => {
              onPatchFile(pack.id, file.id, { content: e.target.value });
              onDirtyChange(key, true);
            }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "s") {
                e.preventDefault();
                void saveContent();
              }
            }}
          />
          <div className="sps-content-footer">
            <span className="sps-hint">
              {isCore
                ? "SKILL.md body — indexed for every ducky; full text loads when needed. Ctrl+S saves."
                : "Reference file — indexed; agents pull the body when the load condition matches. Ctrl+S saves."}
            </span>
            <span style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="sps-btn sps-btn--primary"
                disabled={busy || !dirty}
                onClick={() => void saveContent()}
              >
                {dirty ? "Save file" : "Saved"}
              </button>
            </span>
          </div>
        </div>
      ) : (
        <div className="sps-details-pane">
          <div className="sps-field">
            <label className="sps-label">{isCore ? "Pack label" : "Label"}</label>
            <input
              className="sps-input"
              value={file.title}
              onChange={(e) => onPatchFile(pack.id, file.id, { title: e.target.value })}
              onBlur={() => void saveMeta()}
            />
          </div>
          <div className="sps-field">
            <label className="sps-label">Description</label>
            <textarea
              className="sps-textarea sps-textarea--short"
              value={file.description}
              onChange={(e) => onPatchFile(pack.id, file.id, { description: e.target.value })}
              onBlur={() => void saveMeta()}
            />
            <p className="sps-hint">
              {isCore
                ? "Written to SKILL.md frontmatter — shown in the skill index for every ducky."
                : "Shown in the reference index so agents know when to read this file."}
            </p>
          </div>

          {isCore ? (
            <p className="sps-hint">
              SKILL.md is the pack itself — its label and description are the pack&apos;s name and
              description everywhere (studio, chats, Claude Code, Cursor). All packs are indexed for
              every agent; full text loads on demand. It cannot be deleted.
            </p>
          ) : (
            <>
              <div className="sps-field">
                <label className="sps-label sps-label--emerald">Load condition</label>
                <input
                  className="sps-input sps-input--condition"
                  placeholder="e.g. User asks about troubleshooting"
                  value={file.loadCondition}
                  onChange={(e) => onPatchFile(pack.id, file.id, { loadCondition: e.target.value })}
                  onBlur={() => void saveMeta()}
                />
                <p className="sps-hint">Tells agents when it is worth loading this file.</p>
              </div>
              <div className="sps-delete-row">
                <button
                  type="button"
                  className={confirmDelete ? "sps-delete-btn is-confirm" : "sps-delete-btn"}
                  onClick={() => void handleDelete()}
                >
                  <Icons.Trash className="sps-icon-sm" />
                  {confirmDelete ? "Click again to delete" : "Delete file"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </main>
  );
}

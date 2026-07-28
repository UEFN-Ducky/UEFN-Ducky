import { useEffect, useState } from "react";

import { Icons } from "../../icons/Icons";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import type { VerseTemplate, VerseTemplateFile } from "../templates/types";
import { useVerseTemplates } from "../templates/useVerseTemplates";
import { MiniVerseEditor } from "./MiniVerseEditor";

const DEFAULT_CONTENT = "using { /Verse.org/Simulation }\n\n";

const QUICK_ICONS = ["⚙️", "🔥", "🛡️", "📦", "🌟"];

type Mode = "single" | "pack";

export interface CustomVerseTemplateFormProps {
  open: boolean;
  onClose: () => void;
  onBack: () => void;
  onSaved: (template: VerseTemplate) => void;
  initial?: VerseTemplate | null;
}

function emptyFile(i: number): VerseTemplateFile {
  return { path: i === 0 ? "main.verse" : `file_${i + 1}.verse`, content: DEFAULT_CONTENT };
}

export function CustomVerseTemplateForm({
  open,
  onClose: _onClose,
  onBack,
  onSaved,
  initial = null,
}: CustomVerseTemplateFormProps) {
  const { saveCustom } = useVerseTemplates();
  const { alert } = useConfirmModal();
  const editing = !!initial?.id;
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("⚙️");
  const [mode, setMode] = useState<Mode>("single");
  const [content, setContent] = useState(DEFAULT_CONTENT);
  const [folder, setFolder] = useState("");
  const [files, setFiles] = useState<VerseTemplateFile[]>([emptyFile(0)]);
  const [activeFile, setActiveFile] = useState(0);
  const [saving, setSaving] = useState(false);
  const [nameInvalid, setNameInvalid] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setName(initial.name);
      setIcon(initial.icon || "⚙️");
      const pack = (initial.files?.length ?? 0) > 0;
      setMode(pack ? "pack" : "single");
      setContent(initial.content || DEFAULT_CONTENT);
      setFolder(initial.folder || "");
      setFiles(pack ? initial.files!.map((f) => ({ path: f.path, content: f.content })) : [emptyFile(0)]);
      setActiveFile(0);
    } else {
      setName("");
      setIcon("⚙️");
      setMode("single");
      setContent(DEFAULT_CONTENT);
      setFolder("");
      setFiles([emptyFile(0)]);
      setActiveFile(0);
    }
    setSaving(false);
    setNameInvalid(false);
  }, [open, initial]);

  if (!open) return null;

  const handleSave = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameInvalid(true);
      window.setTimeout(() => setNameInvalid(false), 500);
      return;
    }
    if (mode === "pack") {
      const cleaned = files
        .map((f) => ({ path: f.path.trim().replace(/\\/g, "/"), content: f.content }))
        .filter((f) => f.path);
      if (cleaned.length === 0) {
        await alert({ title: "Files required", message: "Add at least one file path." });
        return;
      }
      const folderName = folder.trim() || trimmedName.replace(/[^a-zA-Z0-9_-]+/g, "") || "Pack";
      setSaving(true);
      try {
        const template = await saveCustom({
          name: trimmedName,
          icon,
          folder: folderName,
          files: cleaned,
          content: cleaned[0]?.content ?? "",
          templateId: initial?.id,
        });
        onSaved(template);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to save template";
        await alert({ title: "Save failed", message });
      } finally {
        setSaving(false);
      }
      return;
    }

    setSaving(true);
    try {
      const template = await saveCustom({
        name: trimmedName,
        icon,
        content,
        templateId: initial?.id,
      });
      onSaved(template);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to save template";
      await alert({ title: "Save failed", message });
    } finally {
      setSaving(false);
    }
  };

  const active = files[activeFile] ?? files[0];
  const editorValue = mode === "single" ? content : (active?.content ?? "");

  return (
    <div className="vtm-view vtm-view--active">
      <div className="vtm-header">
        <div className="vtm-header-left">
          <button type="button" className="vtm-icon-btn vtm-icon-btn--back" onClick={onBack} title="Back to Templates" aria-label="Back">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
          </button>
          <h2 className="vtm-title">{editing ? "Edit Template" : "Design Template"}</h2>
        </div>
        <button type="button" className="vtm-icon-btn" onClick={onBack} aria-label="Close">
          <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div className="vtm-body vtm-creator-body">
        <div className="vtm-form-grid">
          <label className="vtm-field">
            <span className="vtm-label">Template Name</span>
            <input
              type="text"
              id="template-name"
              className={`vtm-input vtm-input--solid${nameInvalid ? " is-invalid" : ""}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Player Core Base"
            />
          </label>

          <div className="vtm-field">
            <span className="vtm-label">Architecture</span>
            <div className="vtm-mode-toggle" role="group" aria-label="Template architecture">
              <button
                type="button"
                className={`vtm-mode-btn${mode === "single" ? " is-selected" : ""}`}
                onClick={() => setMode("single")}
              >
                Single File
              </button>
              <button
                type="button"
                className={`vtm-mode-btn${mode === "pack" ? " is-selected" : ""}`}
                onClick={() => setMode("pack")}
              >
                Multi-file System
              </button>
            </div>
          </div>
        </div>

        <div className="vtm-form-grid">
          <div className="vtm-field">
            <span className="vtm-label">Identifier Icon</span>
            <div className="vtm-icon-row">
              <div className="vtm-quick-icons">
                {QUICK_ICONS.map((emoji) => (
                  <button
                    key={emoji}
                    type="button"
                    className={`vtm-quick-icon${icon === emoji ? " is-selected" : ""}`}
                    onClick={() => setIcon(emoji)}
                    aria-pressed={icon === emoji}
                  >
                    {emoji}
                  </button>
                ))}
              </div>
              <span className="vtm-or">or</span>
              <input
                type="text"
                className="vtm-input vtm-input--solid vtm-emoji-paste"
                value={QUICK_ICONS.includes(icon) ? "" : icon}
                onChange={(e) => {
                  const val = e.target.value.trim();
                  if (val) setIcon(val);
                }}
                placeholder="Paste"
                maxLength={2}
              />
            </div>
          </div>

          <div className={`vtm-field vtm-field--folder${mode === "pack" ? " is-visible" : ""}`}>
            <label className="vtm-label vtm-label--split" htmlFor="folder-name">
              <span>Folder Name</span>
              <span className="vtm-label-hint">Content/Verse/...</span>
            </label>
            <input
              type="text"
              id="folder-name"
              className="vtm-input vtm-input--solid"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="e.g. MySystem"
            />
          </div>
        </div>

        <div className="vtm-field vtm-field--editor">
          <div className="vtm-label vtm-label--split">
            <span>Source Code</span>
            {mode === "pack" && active ? (
              <span className="vtm-file-chip">{active.path || "untitled.verse"}</span>
            ) : null}
          </div>

          <div className={`vtm-ide${mode === "pack" ? " has-sidebar" : ""}`}>
            <div className={`vtm-ide-sidebar${mode === "pack" ? "" : " is-hidden"}`}>
              <div className="vtm-ide-sidebar-head">
                <span>Project Files</span>
                <button
                  type="button"
                  className="vtm-ide-add"
                  title="Add File"
                  onClick={() => {
                    setFiles((prev) => [...prev, emptyFile(prev.length)]);
                    setActiveFile(files.length);
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                </button>
              </div>
              <div className="vtm-ide-file-list">
                {files.map((f, i) => {
                  const isActive = i === activeFile;
                  return (
                    <div
                      key={`${i}-${f.path}`}
                      className={`vtm-ide-file${isActive ? " is-active" : ""}`}
                      onClick={() => setActiveFile(i)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setActiveFile(i);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <svg
                        className="vtm-ide-file-icon"
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                      >
                        <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                        <polyline points="13 2 13 9 20 9" />
                      </svg>
                      <input
                        type="text"
                        className="vtm-ide-path"
                        value={f.path}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => {
                          const path = e.target.value;
                          setFiles((prev) => prev.map((row, j) => (j === i ? { ...row, path } : row)));
                        }}
                        spellCheck={false}
                      />
                      {files.length > 1 ? (
                        <button
                          type="button"
                          className="vtm-ide-remove"
                          title="Delete"
                          aria-label={`Remove ${f.path || "file"}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setFiles((prev) => {
                              const next = prev.filter((_, j) => j !== i);
                              return next.length ? next : [emptyFile(0)];
                            });
                            setActiveFile((prev) => Math.max(0, Math.min(prev, files.length - 2)));
                          }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <line x1="18" y1="6" x2="6" y2="18" />
                            <line x1="6" y1="6" x2="18" y2="18" />
                          </svg>
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="vtm-ide-editor">
              <MiniVerseEditor
                value={editorValue}
                onChange={(text) => {
                  if (mode === "single") {
                    setContent(text);
                    return;
                  }
                  setFiles((prev) =>
                    prev.map((row, j) => (j === activeFile ? { ...row, content: text } : row)),
                  );
                }}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="vtm-footer">
        <button type="button" className="vtm-btn vtm-btn--ghost" onClick={onBack}>
          Cancel
        </button>
        <button
          type="button"
          className="vtm-btn vtm-btn--save"
          disabled={saving}
          onClick={() => void handleSave()}
        >
          {saving ? (
            <>
              <span className="vtm-spin" aria-hidden>
                <Icons.Spinner />
              </span>
              Saving…
            </>
          ) : editing ? (
            "Save Changes"
          ) : (
            "Save to Library"
          )}
        </button>
      </div>
    </div>
  );
}

export const CustomVerseTemplateModal = CustomVerseTemplateForm;

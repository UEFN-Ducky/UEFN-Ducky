import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";

import { Modal } from "../../components/Modal";
import { Icons } from "../../icons/Icons";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import type { VerseTemplate } from "../templates/types";
import { useVerseTemplates } from "../templates/useVerseTemplates";
import { CustomVerseTemplateForm } from "./CustomVerseTemplateModal";

const TEXT_FILE_OPTION_ID = "__text_file__";

interface VerseTemplatePickerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: VerseTemplate) => void;
  onSelectTextFile: () => void;
}

function templateDescription(template: VerseTemplate): string {
  if (template.description) return template.description;
  if (template.kind === "custom") {
    const fileCount = template.files?.length ?? 0;
    if (fileCount > 0) {
      return fileCount > 1 ? "Custom multi-file system pack" : "Custom single-file template";
    }
    return "Your saved custom template";
  }
  if (template.kind === "plugin") {
    return template.pluginId ? `From plugin ${template.pluginId}` : "Plugin template";
  }
  return "";
}

function isSystemPack(template: VerseTemplate): boolean {
  const fileCount = template.files?.length ?? 0;
  return fileCount > 1 || (!!template.folder && fileCount >= 1) || template.kind === "plugin";
}

function fileCountLabel(template: VerseTemplate): number {
  return template.files?.length ?? 1;
}

export function VerseTemplatePicker({ open, onClose, onSelect, onSelectTextFile }: VerseTemplatePickerProps) {
  const { templates, loading, deleteCustom } = useVerseTemplates();
  const { confirm } = useConfirmModal();
  const [view, setView] = useState<"picker" | "creator">("picker");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [editing, setEditing] = useState<VerseTemplate | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!open) {
      setView("picker");
      setSearchQuery("");
      setEditing(null);
      setCreating(false);
      return;
    }
    setSelectedId(templates[0]?.id ?? TEXT_FILE_OPTION_ID);
  }, [open, templates]);

  const selected = selectedId === TEXT_FILE_OPTION_ID ? null : (templates.find((t) => t.id === selectedId) ?? null);
  const canCreate = selectedId === TEXT_FILE_OPTION_ID || !!selected;

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter((t) => {
      const desc = templateDescription(t).toLowerCase();
      return t.name.toLowerCase().includes(q) || desc.includes(q);
    });
  }, [searchQuery, templates]);

  const handleCreate = useCallback(() => {
    if (!canCreate || loading || creating) return;
    setCreating(true);
    window.setTimeout(() => {
      if (selectedId === TEXT_FILE_OPTION_ID) {
        onSelectTextFile();
        onClose();
      } else if (selected) {
        onSelect(selected);
        onClose();
      }
      setCreating(false);
    }, 200);
  }, [canCreate, creating, loading, onClose, onSelect, onSelectTextFile, selected, selectedId]);

  const handleDeleteCustom = useCallback(
    async (template: VerseTemplate, e: MouseEvent) => {
      e.stopPropagation();
      if (
        !(await confirm({
          message: `Delete template "${template.name}"?`,
          confirmLabel: "Delete",
          danger: true,
        }))
      ) {
        return;
      }
      const ok = await deleteCustom(template.id);
      if (ok && selectedId === template.id) {
        setSelectedId(templates.find((t) => t.id !== template.id)?.id ?? TEXT_FILE_OPTION_ID);
      }
    },
    [confirm, deleteCustom, selectedId, templates],
  );

  const openCreateView = useCallback(() => {
    setEditing(null);
    setView("creator");
  }, []);

  const openEditView = useCallback((template: VerseTemplate, e: MouseEvent) => {
    e.stopPropagation();
    setEditing(template);
    setView("creator");
  }, []);

  const backToPicker = useCallback(() => {
    setView("picker");
    setEditing(null);
  }, []);

  const handleClose = useCallback(() => {
    setView("picker");
    setEditing(null);
    onClose();
  }, [onClose]);

  const textSelected = selectedId === TEXT_FILE_OPTION_ID;
  const textMatchesSearch = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    return "text file blank .txt project".includes(q) || q.split(/\s+/).every((p) => "text file blank .txt project".includes(p));
  })();

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={view === "creator" ? "Design Template" : "Create New File"}
      width={768}
      hideHeader
      hideClose
      className="vtm-modal"
      bodyClassName="vtm-modal-body"
    >
      <div className="vtm">
        <div className={`vtm-view${view === "picker" ? " vtm-view--active" : " vtm-view--hidden"}`}>
          <div className="vtm-header">
            <h2 className="vtm-title">Create New File</h2>
            <button type="button" className="vtm-icon-btn" onClick={handleClose} aria-label="Close">
              <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>

          <div className="vtm-search">
            <div className="vtm-search-wrap">
              <span className="vtm-search-icon" aria-hidden>
                <svg className="vtm-search-svg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </span>
              <input
                type="text"
                className="vtm-input vtm-search-input"
                placeholder="Search templates..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                autoFocus={view === "picker"}
              />
            </div>
          </div>

          <div className="vtm-body vtm-picker-body" role="listbox" aria-label="New file templates">
            {loading ? <div className="vtm-status">Loading templates…</div> : null}

            <div className="vtm-grid">
              {filtered.map((template) => {
                const isSelected = template.id === selectedId;
                const system = isSystemPack(template);
                return (
                  <button
                    key={template.id}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={`vtm-card${isSelected ? " is-selected" : ""}`}
                    onClick={() => setSelectedId(template.id)}
                    onDoubleClick={() => {
                      setSelectedId(template.id);
                      onSelect(template);
                      onClose();
                    }}
                  >
                    <span className={`vtm-card-icon${isSelected ? " is-selected" : ""}`} aria-hidden>
                      {template.icon}
                    </span>
                    <span className="vtm-card-body">
                      <span className="vtm-card-name">{template.name}</span>
                      {system ? (
                        <span className="vtm-badge vtm-badge--system">
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
                            <polygon points="12 2 2 7 12 12 22 7 12 2" />
                            <polyline points="2 12 12 17 22 12" />
                            <polyline points="2 17 12 22 22 17" />
                          </svg>
                          System ({fileCountLabel(template)})
                        </span>
                      ) : (
                        <span className="vtm-badge">
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                            <polyline points="13 2 13 9 20 9" />
                          </svg>
                          Single File
                        </span>
                      )}
                    </span>
                    {isSelected ? (
                      <span className="vtm-card-check" aria-hidden>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      </span>
                    ) : null}
                    {template.kind === "custom" ? (
                      <span className="vtm-card-actions">
                        <span
                          role="button"
                          tabIndex={0}
                          className="vtm-card-action"
                          aria-label={`Edit ${template.name}`}
                          title={`Edit ${template.name}`}
                          onClick={(e) => openEditView(template, e)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              e.stopPropagation();
                              openEditView(template, e as unknown as MouseEvent);
                            }
                          }}
                        >
                          <Icons.Pencil />
                        </span>
                        <span
                          role="button"
                          tabIndex={0}
                          className="vtm-card-action vtm-card-action--danger"
                          aria-label={`Delete ${template.name}`}
                          title={`Delete ${template.name}`}
                          onClick={(e) => void handleDeleteCustom(template, e)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              e.stopPropagation();
                              void handleDeleteCustom(template, e as unknown as MouseEvent);
                            }
                          }}
                        >
                          <Icons.Trash />
                        </span>
                      </span>
                    ) : null}
                  </button>
                );
              })}

              {textMatchesSearch ? (
                <button
                  type="button"
                  role="option"
                  aria-selected={textSelected}
                  className={`vtm-card${textSelected ? " is-selected" : ""}`}
                  onClick={() => setSelectedId(TEXT_FILE_OPTION_ID)}
                  onDoubleClick={() => {
                    onSelectTextFile();
                    onClose();
                  }}
                >
                  <span className={`vtm-card-icon${textSelected ? " is-selected" : ""}`} aria-hidden>
                    📝
                  </span>
                  <span className="vtm-card-body">
                    <span className="vtm-card-name">Text file</span>
                    <span className="vtm-badge">
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                        <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                        <polyline points="13 2 13 9 20 9" />
                      </svg>
                      Single File
                    </span>
                  </span>
                  {textSelected ? (
                    <span className="vtm-card-check" aria-hidden>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M20 6L9 17l-5-5" />
                      </svg>
                    </span>
                  ) : null}
                </button>
              ) : null}

              {!loading && filtered.length === 0 && !textMatchesSearch ? (
                <div className="vtm-empty">
                  <div className="vtm-empty-icon" aria-hidden>
                    🔍
                  </div>
                  <p className="vtm-empty-text">No matching templates</p>
                </div>
              ) : null}
            </div>

            <div className="vtm-create-custom">
              <button type="button" className="vtm-create-custom-btn" onClick={openCreateView}>
                <span className="vtm-create-custom-icon" aria-hidden>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                </span>
                <span className="vtm-create-custom-body">
                  <span className="vtm-create-custom-name">Create Custom Template</span>
                  <span className="vtm-create-custom-desc">Design a single file or a multi-file system pack</span>
                </span>
              </button>
            </div>
          </div>

          <div className="vtm-footer">
            <button type="button" className="vtm-btn vtm-btn--ghost" onClick={handleClose}>
              Cancel
            </button>
            <button
              type="button"
              className="vtm-btn vtm-btn--primary"
              disabled={!canCreate || loading || creating}
              onClick={handleCreate}
            >
              {creating ? (
                <>
                  <span className="vtm-spin" aria-hidden>
                    <Icons.Spinner />
                  </span>
                  Processing...
                </>
              ) : (
                "Create File"
              )}
            </button>
          </div>
        </div>

        {view === "creator" ? (
          <CustomVerseTemplateForm
            open
            initial={editing}
            onClose={handleClose}
            onBack={backToPicker}
            onSaved={(template) => {
              setSelectedId(template.id);
              setEditing(null);
              setView("picker");
            }}
          />
        ) : null}
      </div>
    </Modal>
  );
}

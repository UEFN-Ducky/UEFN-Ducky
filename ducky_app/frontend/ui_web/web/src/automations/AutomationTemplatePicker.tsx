import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";

import { Modal } from "../components/Modal";
import { Icons } from "../icons/Icons";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import type { AutomationGraphDto, AutomationTemplateDto } from "../types/panel";

const BLANK_ID = "__blank__";

interface AutomationTemplatePickerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: AutomationTemplateDto | null) => void;
  currentGraph?: AutomationGraphDto | null;
}

export function AutomationTemplatePicker({
  open,
  onClose,
  onSelect,
  currentGraph,
}: AutomationTemplatePickerProps) {
  const { confirm } = useConfirmModal();
  const [view, setView] = useState<"picker" | "creator">("picker");
  const [templates, setTemplates] = useState<AutomationTemplateDto[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(BLANK_ID);
  const [searchQuery, setSearchQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AutomationTemplateDto | null>(null);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.list_automation_templates) {
      setTemplates([]);
      return;
    }
    setLoading(true);
    try {
      const res = await api.list_automation_templates();
      setTemplates(res?.templates || []);
    } catch {
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      setView("picker");
      setSearchQuery("");
      setEditing(null);
      setCreating(false);
      setFormError("");
      return;
    }
    void refresh();
    setSelectedId(BLANK_ID);
  }, [open, refresh]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter((t) =>
      `${t.name} ${t.label || ""} ${t.description || ""} ${t.plugin_id || ""}`.toLowerCase().includes(q),
    );
  }, [searchQuery, templates]);

  const selected = selectedId === BLANK_ID ? null : templates.find((t) => t.id === selectedId) || null;
  const blankMatches = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;
    return "blank empty workflow".includes(q);
  })();

  const handleCreate = useCallback(() => {
    if (loading || creating) return;
    setCreating(true);
    window.setTimeout(() => {
      onSelect(selectedId === BLANK_ID ? null : selected);
      onClose();
      setCreating(false);
    }, 200);
  }, [creating, loading, onClose, onSelect, selected, selectedId]);

  const openCreateView = useCallback((row: AutomationTemplateDto | null) => {
    setEditing(row);
    setFormName(row?.name || "");
    setFormDesc(row?.description || "");
    setFormError("");
    setView("creator");
  }, []);

  const handleDelete = useCallback(
    async (template: AutomationTemplateDto, e: MouseEvent) => {
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
      const ok = await getApi()?.delete_custom_automation_template?.(template.id);
      if (ok?.ok) {
        setTemplates((prev) => prev.filter((t) => t.id !== template.id));
        if (selectedId === template.id) setSelectedId(BLANK_ID);
      }
    },
    [confirm, selectedId],
  );

  const handleSaveCustom = useCallback(async () => {
    const name = formName.trim();
    if (!name) {
      setFormError("Name is required");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const graph = editing?.graph || currentGraph || { nodes: [], edges: [] };
      const res = await getApi()?.save_custom_automation_template?.(
        name,
        formDesc,
        editing?.icon || "⚡",
        JSON.stringify(graph),
        editing?.kind === "custom" ? editing.id : "",
      );
      if (!res?.ok || !res.template) {
        setFormError(res?.error || "Could not save");
        return;
      }
      await refresh();
      setSelectedId(res.template.id);
      setView("picker");
      setEditing(null);
    } finally {
      setSaving(false);
    }
  }, [currentGraph, editing, formDesc, formName, refresh]);

  const handleClose = useCallback(() => {
    setView("picker");
    setEditing(null);
    onClose();
  }, [onClose]);

  const hasOpenGraph = (currentGraph?.nodes?.length || 0) > 0;

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={view === "creator" ? "Save Template" : "Create Automation"}
      width={768}
      hideHeader
      hideClose
      className="vtm-modal"
      bodyClassName="vtm-modal-body"
    >
      <div className="vtm">
        <div className={`vtm-view${view === "picker" ? " vtm-view--active" : " vtm-view--hidden"}`}>
          <div className="vtm-header">
            <h2 className="vtm-title">Create Automation</h2>
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
          <div className="vtm-body vtm-picker-body" role="listbox" aria-label="Automation templates">
            {loading ? <div className="vtm-status">Loading templates…</div> : null}
            <div className="vtm-grid">
              {blankMatches ? (
                <button
                  type="button"
                  role="option"
                  aria-selected={selectedId === BLANK_ID}
                  className={`vtm-card${selectedId === BLANK_ID ? " is-selected" : ""}`}
                  onClick={() => setSelectedId(BLANK_ID)}
                  onDoubleClick={() => {
                    onSelect(null);
                    onClose();
                  }}
                >
                  <span className={`vtm-card-icon${selectedId === BLANK_ID ? " is-selected" : ""}`} aria-hidden>
                    ∅
                  </span>
                  <span className="vtm-card-body">
                    <span className="vtm-card-name">Blank workflow</span>
                    <span className="vtm-badge">Empty canvas</span>
                  </span>
                  {selectedId === BLANK_ID ? (
                    <span className="vtm-card-check" aria-hidden>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M20 6L9 17l-5-5" />
                      </svg>
                    </span>
                  ) : null}
                </button>
              ) : null}
              {filtered.map((template) => {
                const isSelected = template.id === selectedId;
                const plugin = template.kind === "plugin";
                return (
                  <button
                    key={template.id}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={`vtm-card${isSelected ? " is-selected" : ""}`}
                    onClick={() => setSelectedId(template.id)}
                    onDoubleClick={() => {
                      onSelect(template);
                      onClose();
                    }}
                  >
                    <span className={`vtm-card-icon${isSelected ? " is-selected" : ""}`} aria-hidden>
                      {template.icon || "⚡"}
                    </span>
                    <span className="vtm-card-body">
                      <span className="vtm-card-name">{template.name}</span>
                      <span className={`vtm-badge${plugin ? " vtm-badge--system" : ""}`}>
                        {plugin ? `Plugin (${template.plugin_id || "store"})` : "Yours"}
                      </span>
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
                          onClick={(e) => {
                            e.stopPropagation();
                            openCreateView(template);
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
                          onClick={(e) => void handleDelete(template, e)}
                        >
                          <Icons.Trash />
                        </span>
                      </span>
                    ) : null}
                  </button>
                );
              })}
              {!loading && filtered.length === 0 && !blankMatches ? (
                <div className="vtm-empty">
                  <div className="vtm-empty-icon" aria-hidden>
                    🔍
                  </div>
                  <p className="vtm-empty-text">No matching templates</p>
                </div>
              ) : null}
            </div>
            <div className="vtm-create-custom">
              <button type="button" className="vtm-create-custom-btn" onClick={() => openCreateView(null)}>
                <span className="vtm-create-custom-icon" aria-hidden>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                </span>
                <span className="vtm-create-custom-body">
                  <span className="vtm-create-custom-name">Save as template</span>
                  <span className="vtm-create-custom-desc">
                    {hasOpenGraph ? "Keep the open workflow as a reusable template" : "Name a blank template you can fill in later"}
                  </span>
                </span>
              </button>
            </div>
          </div>
          <div className="vtm-footer">
            <button type="button" className="vtm-btn vtm-btn--ghost" onClick={handleClose}>
              Cancel
            </button>
            <button type="button" className="vtm-btn vtm-btn--primary" disabled={loading || creating} onClick={handleCreate}>
              {creating ? (
                <>
                  <span className="vtm-spin" aria-hidden>
                    <Icons.Spinner />
                  </span>
                  Processing...
                </>
              ) : (
                "Create Automation"
              )}
            </button>
          </div>
        </div>

        {view === "creator" ? (
          <div className="vtm-view vtm-view--active">
            <div className="vtm-header">
              <div className="vtm-header-left">
                <button
                  type="button"
                  className="vtm-icon-btn vtm-icon-btn--back"
                  onClick={() => {
                    setView("picker");
                    setEditing(null);
                  }}
                  title="Back to Templates"
                  aria-label="Back"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="15 18 9 12 15 6" />
                  </svg>
                </button>
                <h2 className="vtm-title">{editing ? "Edit Template" : "Save Template"}</h2>
              </div>
              <button type="button" className="vtm-icon-btn" onClick={handleClose} aria-label="Close">
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
                    className={`vtm-input vtm-input--solid${formError && !formName.trim() ? " is-invalid" : ""}`}
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    placeholder="Discord starts a ducky"
                  />
                </label>
                <label className="vtm-field">
                  <span className="vtm-label">Description</span>
                  <input
                    className="vtm-input vtm-input--solid"
                    value={formDesc}
                    onChange={(e) => setFormDesc(e.target.value)}
                    placeholder="What this graph does"
                  />
                </label>
              </div>
              {formError ? <p className="vtm-empty-text">{formError}</p> : null}
              <p className="vtm-create-custom-desc">
                {editing
                  ? "Rename only — graph stays as saved."
                  : hasOpenGraph
                    ? "Saves the workflow currently open on the canvas."
                    : "Saves an empty graph. Build it on the canvas, then save again."}
              </p>
            </div>
            <div className="vtm-footer">
              <button
                type="button"
                className="vtm-btn vtm-btn--ghost"
                onClick={() => {
                  setView("picker");
                  setEditing(null);
                }}
              >
                Back
              </button>
              <button type="button" className="vtm-btn vtm-btn--save" disabled={saving} onClick={() => void handleSaveCustom()}>
                {saving ? "Saving…" : "Save Template"}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

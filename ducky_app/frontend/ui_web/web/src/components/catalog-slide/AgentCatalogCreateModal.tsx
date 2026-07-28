import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Modal, ModalActions } from "../Modal";
import { ModelSelector } from "../ModelSelector";
import { getCachedDefaultModel } from "../../hooks/modelsCatalogCache";

export type AgentCatalogCreateDomain = "memory" | "plans" | "skills" | "mcps";

type Props = {
  open: boolean;
  title: string;
  nameLabel: string;
  namePlaceholder: string;
  descriptionLabel: string;
  descriptionPlaceholder: string;
  generateLabel?: string;
  emptyLabel?: string;
  saveLabel?: string;
  busy?: boolean;
  /** Extra fields below description (domain-specific). */
  extraFields?: ReactNode;
  onClose: () => void;
  /** AI generate — returns optional checklist lines to animate. */
  onGenerate: (args: {
    name: string;
    description: string;
    model: string;
  }) => Promise<{ checklist?: string[] } | void>;
  /** Persist empty / after generate. */
  onSave: (args: {
    name: string;
    description: string;
    generated: boolean;
  }) => Promise<void>;
  /** Skip generate — create empty shell then save. */
  allowEmpty?: boolean;
};

/**
 * Shared “ask agent” create modal (Skills New pack pattern) for memory / plans / skills / MCPs.
 */
export function AgentCatalogCreateModal({
  open,
  title,
  nameLabel,
  namePlaceholder,
  descriptionLabel,
  descriptionPlaceholder,
  generateLabel = "Generate",
  emptyLabel = "Create empty instead",
  saveLabel = "Save & close",
  busy = false,
  extraFields,
  onClose,
  onGenerate,
  onSave,
  allowEmpty = true,
}: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(() => getCachedDefaultModel());
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [checklist, setChecklist] = useState<Array<{ label: string; done: boolean }>>([]);
  const [error, setError] = useState("");

  const reset = useCallback(() => {
    setName("");
    setDescription("");
    setGenerating(false);
    setSaving(false);
    setGenerated(false);
    setChecklist([]);
    setError("");
  }, []);

  useEffect(() => {
    if (open) {
      reset();
      setModel(getCachedDefaultModel());
    }
  }, [open, reset]);

  const canGenerate = !!description.trim();
  const canSave = generated || (!!name.trim() && !!description.trim());

  const handleGenerate = async () => {
    if (!canGenerate || generating || busy) return;
    setError("");
    setGenerating(true);
    setChecklist([{ label: "Generating…", done: false }]);
    try {
      const res = await onGenerate({
        name: name.trim(),
        description: description.trim(),
        model,
      });
      const lines = res?.checklist?.length ? res.checklist : ["Ready"];
      setChecklist(lines.map((label) => ({ label, done: true })));
      setGenerated(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setChecklist([]);
      setGenerated(false);
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async (asEmpty: boolean) => {
    if (saving || busy) return;
    setError("");
    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        generated: asEmpty ? false : generated,
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      width={520}
      footer={
        <ModalActions
          cancelLabel="Cancel"
          confirmLabel={saving ? "Saving…" : saveLabel}
          confirmDisabled={!canSave || generating || saving || busy}
          onCancel={onClose}
          onConfirm={() => void handleSave(false)}
        />
      }
    >
      <div className="catalog-slide-create">
        <label className="catalog-slide-create-field">
          <span>{nameLabel}</span>
          <input
            className="catalog-slide-search-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={namePlaceholder}
            disabled={generating || saving || busy}
          />
        </label>
        <label className="catalog-slide-create-field">
          <span>{descriptionLabel}</span>
          <textarea
            className="catalog-slide-create-textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={descriptionPlaceholder}
            rows={4}
            disabled={generating || saving || busy}
          />
        </label>
        {extraFields}
        <div className="catalog-slide-create-gen-row">
          <ModelSelector
            selectedModel={model}
            setSelectedModel={setModel}
            menuPlacement="bottom"
          />
          <button
            type="button"
            className="catalog-slide-action catalog-slide-action--primary"
            disabled={!canGenerate || generating || saving || busy}
            onClick={() => void handleGenerate()}
          >
            {generating ? "Generating…" : generateLabel}
          </button>
        </div>
        {allowEmpty ? (
          <button
            type="button"
            className="catalog-slide-create-empty-link"
            disabled={generating || saving || busy || !name.trim()}
            onClick={() => void handleSave(true)}
          >
            {emptyLabel}
          </button>
        ) : null}
        {checklist.length ? (
          <ul className="catalog-slide-create-checklist">
            {checklist.map((item, i) => (
              <li key={i} className={item.done ? "is-done" : "is-active"}>
                {item.label}
              </li>
            ))}
          </ul>
        ) : null}
        {error ? <p className="catalog-slide-create-error">{error}</p> : null}
      </div>
    </Modal>
  );
}

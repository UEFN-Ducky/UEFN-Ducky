import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal, ModalActions } from "../../components/Modal";
import { ModelSelector } from "../../components/ModelSelector";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getCachedDefaultModel, getCachedModels } from "../../hooks/modelsCatalogCache";
import {
  createFileWithContent,
  draftReferenceFile,
  packSlugFromLabel,
} from "../api/skillPackStudioApi";

interface CreateReferenceFileModalProps {
  open: boolean;
  packId: string | null;
  packLabel: string;
  existingFileIds: string[];
  busy: boolean;
  onClose: () => void;
  onCreated: (packId: string, fileId: string) => void;
}

function providerForModel(modelId: string): string {
  const row = getCachedModels()?.find((m) => m.id === modelId);
  return row?.providerKey ?? "";
}

function emptyMarkdown(label: string, description: string): string {
  const body = description.trim() || "Add reference guidance for the agent here.";
  return `# ${label}\n\n${body}\n`;
}

export function CreateReferenceFileModal({
  open,
  packId,
  packLabel,
  existingFileIds,
  busy,
  onClose,
  onCreated,
}: CreateReferenceFileModalProps) {
  const { confirm, alert } = useConfirmModal();
  const [fileLabel, setFileLabel] = useState("");
  const [description, setDescription] = useState("");
  const [selectedModel, setSelectedModel] = useState(() => getCachedDefaultModel());
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [previewMarkdown, setPreviewMarkdown] = useState("");
  const [resolvedFileId, setResolvedFileId] = useState("");

  const reset = useCallback(() => {
    setFileLabel("");
    setDescription("");
    setPreviewMarkdown("");
    setResolvedFileId("");
    setError("");
    setGenerating(false);
    setSaving(false);
  }, []);

  useEffect(() => {
    if (open) {
      reset();
      setSelectedModel(getCachedDefaultModel());
    }
  }, [open, reset]);

  const slugPreview = useMemo(() => {
    const label = fileLabel.trim();
    return label ? packSlugFromLabel(label) : "";
  }, [fileLabel]);

  const idConflict = !!slugPreview && existingFileIds.includes(slugPreview);

  const handleClose = useCallback(async () => {
    if (generating || saving) return;
    if (!fileLabel.trim() && !description.trim() && !previewMarkdown) {
      onClose();
      return;
    }
    const ok = await confirm({
      message: "Discard this reference file draft?",
      confirmLabel: "Discard",
      danger: true,
    });
    if (!ok) return;
    onClose();
  }, [confirm, description, fileLabel, generating, onClose, previewMarkdown, saving]);

  const handleGenerate = useCallback(async () => {
    const label = fileLabel.trim();
    const topic = description.trim();
    if (!label || !topic || !packId) return;
    if (!selectedModel) {
      await alert({ title: "No model", message: "Pick a model in the selector below." });
      return;
    }
    if (idConflict) {
      setError(`File id "${slugPreview}" already exists in this pack.`);
      return;
    }
    setError("");
    setGenerating(true);
    setPreviewMarkdown("");
    setResolvedFileId("");
    try {
      const provider = providerForModel(selectedModel);
      const draft = await draftReferenceFile(packId, label, topic, selectedModel, provider);
      if (existingFileIds.includes(draft.id)) {
        throw new Error(`File id "${draft.id}" already exists in this pack.`);
      }
      setPreviewMarkdown(draft.markdown);
      setResolvedFileId(draft.id);
      if (draft.label && draft.label !== label) setFileLabel(draft.label);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }, [
    alert,
    description,
    existingFileIds,
    fileLabel,
    idConflict,
    packId,
    selectedModel,
    slugPreview,
  ]);

  const handleCreateEmpty = useCallback(() => {
    const label = fileLabel.trim();
    if (!label) {
      void alert({ title: "Name required", message: "Enter a file name first." });
      return;
    }
    if (idConflict) {
      setError(`File id "${slugPreview}" already exists in this pack.`);
      return;
    }
    setError("");
    setPreviewMarkdown(emptyMarkdown(label, description));
    setResolvedFileId(slugPreview);
  }, [alert, description, fileLabel, idConflict, slugPreview]);

  const handleSave = useCallback(async () => {
    if (!packId || !fileLabel.trim() || !previewMarkdown || idConflict) return;
    setSaving(true);
    setError("");
    try {
      const fileId = await createFileWithContent(
        packId,
        fileLabel.trim(),
        description.trim() || fileLabel.trim(),
        previewMarkdown,
        resolvedFileId || slugPreview || undefined,
      );
      onCreated(packId, fileId);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [description, fileLabel, idConflict, onClose, onCreated, packId, previewMarkdown, resolvedFileId, slugPreview]);

  const canGenerate = !!fileLabel.trim() && !!description.trim();
  const canCreateEmpty = !!fileLabel.trim();
  const saveDisabled = !previewMarkdown || idConflict || saving || generating || busy;

  if (!packId) return null;

  return (
    <Modal
      open={open}
      onClose={() => void handleClose()}
      title={`Add reference — ${packLabel}`}
      width={520}
      footer={
        <ModalActions
          cancelLabel="Cancel"
          confirmLabel={saving ? "Adding…" : "Add file"}
          confirmDisabled={saveDisabled}
          onCancel={() => void handleClose()}
          onConfirm={() => void handleSave()}
        />
      }
    >
      <div className="sps-create-modal">
        <div className="sps-create-field">
          <label className="sps-create-label" htmlFor="sps-ref-name">
            File name
          </label>
          <input
            id="sps-ref-name"
            className="sps-create-input"
            type="text"
            placeholder="e.g. Troubleshooting"
            value={fileLabel}
            spellCheck={false}
            disabled={generating || saving}
            onChange={(e) => setFileLabel(e.target.value)}
          />
          {slugPreview ? (
            <span className={`sps-create-slug-hint${idConflict ? " is-error" : ""}`}>
              references/{slugPreview}.md{idConflict ? " — already exists" : ""}
            </span>
          ) : null}
        </div>

        <div className="sps-create-field">
          <label className="sps-create-label" htmlFor="sps-ref-desc">
            What should this file cover?
          </label>
          <textarea
            id="sps-ref-desc"
            className="sps-create-textarea"
            rows={3}
            placeholder="e.g. Common listener offline errors, how to verify MCP connection, and recovery steps…"
            value={description}
            spellCheck={false}
            disabled={generating || saving}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="sps-create-toolbar">
          <ModelSelector
            selectedModel={selectedModel}
            setSelectedModel={setSelectedModel}
            requireTools
          />
          <button
            type="button"
            className="sps-btn sps-btn--primary sps-btn--compact"
            disabled={!canGenerate || generating || saving || busy || idConflict}
            onClick={() => void handleGenerate()}
          >
            {generating ? "Generating…" : "Generate"}
          </button>
        </div>

        <button
          type="button"
          className="sps-create-empty-link"
          disabled={!canCreateEmpty || generating || saving || idConflict}
          onClick={handleCreateEmpty}
        >
          Create empty file instead
        </button>

        {previewMarkdown ? (
          <div className="sps-create-preview">
            <span className="sps-create-label">Preview</span>
            <pre className="sps-create-preview-body">{previewMarkdown.slice(0, 600)}{previewMarkdown.length > 600 ? "…" : ""}</pre>
          </div>
        ) : null}

        {error ? <p className="sps-create-error">{error}</p> : null}
      </div>
    </Modal>
  );
}

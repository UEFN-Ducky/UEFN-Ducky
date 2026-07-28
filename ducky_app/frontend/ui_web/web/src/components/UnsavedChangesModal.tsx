import { Modal } from "./Modal";
import type { ReactNode } from "react";

interface UnsavedChangesModalProps {
  fileName?: string;
  message?: ReactNode;
  saving?: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}

export function UnsavedChangesModal({
  fileName,
  message,
  saving = false,
  onSave,
  onDiscard,
  onCancel,
}: UnsavedChangesModalProps) {
  return (
    <Modal
      open
      onClose={onCancel}
      title="Save changes?"
      width={420}
      footer={
        <div className="modal-actions">
          <button type="button" className="modal-btn modal-btn-muted" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="modal-btn modal-btn-danger" onClick={onDiscard} disabled={saving}>
            Don&apos;t save
          </button>
          <button type="button" className="modal-btn modal-btn-primary" onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      }
    >
      <p className="confirm-modal-message">
        {message ?? (
          <>
            <strong>{fileName}</strong> has unsaved changes. Save before closing?
          </>
        )}
      </p>
    </Modal>
  );
}

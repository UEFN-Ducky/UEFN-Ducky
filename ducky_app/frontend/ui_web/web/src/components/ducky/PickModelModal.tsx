import { useEffect, useState } from "react";
import { Modal, ModalActions } from "../Modal";
import { DuckyModelPicker } from "./DuckyModelPicker";
import { validateModelSelection } from "./duckyProfileForm";

interface PickModelModalProps {
  open: boolean;
  title?: string;
  message: string;
  initialModel: string;
  confirmLabel?: string;
  /** Hide when already on Settings → Duckies. */
  showSettingsLink?: boolean;
  onClose: () => void;
  onConfirm: (model: string) => void;
  onOpenProfileSettings?: () => void;
  /** Jump to Settings → LLMs to set the global Default Model instead. */
  onOpenLlmSettings?: () => void;
}

/** Gate shown when a Ducky could not resolve a model (none picked, no default). */
export function PickModelModal({
  open,
  title = "Pick a model",
  message,
  initialModel,
  confirmLabel = "Continue",
  showSettingsLink = true,
  onClose,
  onConfirm,
  onOpenProfileSettings,
  onOpenLlmSettings,
}: PickModelModalProps) {
  const [model, setModel] = useState(() => (initialModel || "").trim());
  // The backend already failed to resolve, so continuing needs a concrete pick.
  const pickError = model.trim()
    ? validateModelSelection(model)
    : "Pick a model to continue, or set a Default Model in Settings → LLMs.";

  useEffect(() => {
    if (open) setModel((initialModel || "").trim());
  }, [open, initialModel]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width={460}
      zIndex={100005}
      footer={
        <div className="pick-first-choice-modal-footer">
          <span className="pick-first-choice-modal-links">
            {showSettingsLink && onOpenProfileSettings ? (
              <button type="button" className="settings-btn" onClick={onOpenProfileSettings}>
                Open Duckies settings
              </button>
            ) : null}
            {onOpenLlmSettings ? (
              <button type="button" className="settings-btn" onClick={onOpenLlmSettings}>
                Set Default Model…
              </button>
            ) : null}
          </span>
          <ModalActions
            cancelLabel="Cancel"
            confirmLabel={confirmLabel}
            confirmDisabled={!!pickError}
            onCancel={onClose}
            onConfirm={() => {
              if (pickError) return;
              onConfirm(model.trim());
            }}
          />
        </div>
      }
    >
      <div className="pick-first-choice-modal">
        <p className="confirm-modal-message">{message}</p>
        <DuckyModelPicker
          model={model}
          onChange={setModel}
          allowClear={false}
          placeholder="Pick a model"
          hint=""
          menuPlacement="bottom"
        />
      </div>
    </Modal>
  );
}

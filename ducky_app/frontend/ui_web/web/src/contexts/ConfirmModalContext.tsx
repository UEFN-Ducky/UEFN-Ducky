import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { Modal, ModalActions } from "../components/Modal";

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

export interface AlertOptions {
  title?: string;
  message: string;
  okLabel?: string;
}

type DialogState =
  | { type: "confirm"; options: ConfirmOptions; resolve: (confirmed: boolean) => void }
  | { type: "alert"; options: AlertOptions; resolve: () => void };

interface ConfirmModalContextValue {
  confirm: (options: ConfirmOptions | string) => Promise<boolean>;
  alert: (options: AlertOptions | string) => Promise<void>;
}

const ConfirmModalContext = createContext<ConfirmModalContextValue | null>(null);

function normalizeConfirm(options: ConfirmOptions | string): ConfirmOptions {
  return typeof options === "string" ? { message: options } : options;
}

function normalizeAlert(options: AlertOptions | string): AlertOptions {
  return typeof options === "string" ? { message: options } : options;
}

function confirmShortcutKey(label: string): string | null {
  const match = label.trim().match(/[a-z0-9]/i);
  return match ? match[0].toLowerCase() : null;
}

export function ConfirmModalProvider({ children }: { children: ReactNode }) {
  const [dialog, setDialog] = useState<DialogState | null>(null);

  const confirm = useCallback((options: ConfirmOptions | string): Promise<boolean> => {
    const opts = normalizeConfirm(options);
    return new Promise((resolve) => {
      setDialog({ type: "confirm", options: opts, resolve });
    });
  }, []);

  const alert = useCallback((options: AlertOptions | string): Promise<void> => {
    const opts = normalizeAlert(options);
    return new Promise((resolve) => {
      setDialog({ type: "alert", options: opts, resolve });
    });
  }, []);

  const dismissConfirm = (confirmed: boolean) => {
    if (dialog?.type !== "confirm") return;
    dialog.resolve(confirmed);
    setDialog(null);
  };

  const dismissAlert = () => {
    if (dialog?.type !== "alert") return;
    dialog.resolve();
    setDialog(null);
  };

  const title =
    dialog?.type === "confirm"
      ? (dialog.options.title ?? "Confirm")
      : dialog?.type === "alert"
        ? (dialog.options.title ?? "Notice")
        : "";

  const message = dialog?.options.message ?? "";
  const confirmLabel = dialog?.type === "confirm" ? (dialog.options.confirmLabel ?? "OK") : "";
  const confirmKey = dialog?.type === "confirm" ? confirmShortcutKey(confirmLabel) : null;

  useEffect(() => {
    if (!dialog || dialog.type !== "confirm" || !confirmKey) return;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        target.closest('input, textarea, select, [contenteditable="true"]')
      ) {
        return;
      }

      const key = e.key.toLowerCase();
      if (key === "c") {
        e.preventDefault();
        dialog.resolve(false);
        setDialog(null);
      } else if (key === confirmKey) {
        e.preventDefault();
        dialog.resolve(true);
        setDialog(null);
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [dialog, confirmKey]);

  return (
    <ConfirmModalContext.Provider value={{ confirm, alert }}>
      {children}
      {dialog ? (
        <Modal
          open
          onClose={() => (dialog.type === "confirm" ? dismissConfirm(false) : dismissAlert())}
          title={title}
          width={420}
          footer={
            dialog.type === "confirm" ? (
              <ModalActions
                cancelLabel={`${dialog.options.cancelLabel ?? "Cancel"} (C)`}
                confirmLabel={
                  confirmKey
                    ? `${confirmLabel} (${confirmKey.toUpperCase()})`
                    : confirmLabel
                }
                danger={dialog.options.danger}
                onCancel={() => dismissConfirm(false)}
                onConfirm={() => dismissConfirm(true)}
              />
            ) : (
              <ModalActions
                hideCancel
                confirmLabel={dialog.options.okLabel ?? "OK"}
                onCancel={dismissAlert}
                onConfirm={dismissAlert}
              />
            )
          }
        >
          <p className="confirm-modal-message">{message}</p>
        </Modal>
      ) : null}
    </ConfirmModalContext.Provider>
  );
}

export function useConfirmModal(): ConfirmModalContextValue {
  const ctx = useContext(ConfirmModalContext);
  if (!ctx) {
    throw new Error("useConfirmModal must be used within ConfirmModalProvider");
  }
  return ctx;
}

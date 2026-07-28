import { createPortal } from "react-dom";
import { useRef, type ReactNode } from "react";

import { useFocusTrap } from "../hooks/useFocusTrap";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  hideHeader?: boolean;
  /** When hideHeader, skip the auto close button (children supply their own chrome). */
  hideClose?: boolean;
  floatingClose?: boolean;
  className?: string;
  bodyClassName?: string;
  /** Raise the backdrop above other overlays (e.g. a dropdown panel at 100010). */
  zIndex?: number;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 400,
  hideHeader = false,
  hideClose = false,
  floatingClose = false,
  className = "",
  bodyClassName = "",
  zIndex,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const scopeClass = useScopedClass("modal-panel");
  const backdropClass = useScopedClass("modal-backdrop");

  useFocusTrap(panelRef, open, { onEscape: onClose });

  if (!open) return null;

  const showAutoClose = hideHeader && !hideClose;
  const panelClass = [
    "modal-panel",
    "is-sized",
    hideHeader ? "modal-panel--no-header" : "",
    className,
    scopeClass,
  ]
    .filter(Boolean)
    .join(" ");
  const bodyClass = ["modal-body", showAutoClose ? "modal-body--flush-top" : "", bodyClassName]
    .filter(Boolean)
    .join(" ");

  return createPortal(
    <div
      className={`modal-backdrop no-drag ${backdropClass}`}
      onMouseDown={(e) => {
        if (e.button !== 0) return;
        const target = e.target as Node;
        if (target instanceof Element && target.closest(".dropdown-panel, .context-menu")) return;
        if (panelRef.current && !panelRef.current.contains(target)) {
          onClose();
        }
      }}
    >
      <ScopedCss selector={`.${scopeClass}`} rules={{ "--modal-panel-width": `${width}px` }} />
      {zIndex !== undefined ? (
        <ScopedCss selector={`.${backdropClass}`} rules={{ "z-index": String(zIndex) }} />
      ) : null}
      <div
        ref={panelRef}
        className={panelClass}
        role="dialog"
        aria-modal="true"
        aria-label={hideHeader ? title : undefined}
      >
        {showAutoClose ? (
          <button
            type="button"
            className={`icon-btn modal-close${floatingClose ? " modal-close--floating" : ""}`}
            onClick={onClose}
            aria-label="Close"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" fill="none" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        ) : null}
        {!hideHeader ? (
          <div className="modal-header">
            <h2 className="modal-title">{title}</h2>
            <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label="Close">
              <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        ) : null}
        <div className={bodyClass}>{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

interface ModalActionsProps {
  onCancel: () => void;
  onConfirm: () => void;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmDisabled?: boolean;
  danger?: boolean;
  hideCancel?: boolean;
  /** Extra class on the confirm button (e.g. dirty/clean save styling). */
  confirmClassName?: string;
}

export function ModalActions({
  onCancel,
  onConfirm,
  confirmLabel = "Create",
  cancelLabel = "Cancel",
  confirmDisabled = false,
  danger = false,
  hideCancel = false,
  confirmClassName = "",
}: ModalActionsProps) {
  return (
    <div className="modal-actions-row">
      {!hideCancel ? (
        <button type="button" className="settings-btn" onClick={onCancel}>
          {cancelLabel}
        </button>
      ) : null}
      <button
        type="button"
        className={`settings-btn modal-confirm-btn${confirmDisabled ? " is-disabled" : ""}${danger ? " is-danger" : ""}${confirmClassName ? ` ${confirmClassName}` : ""}`}
        onClick={onConfirm}
        disabled={confirmDisabled}
      >
        {confirmLabel}
      </button>
    </div>
  );
}

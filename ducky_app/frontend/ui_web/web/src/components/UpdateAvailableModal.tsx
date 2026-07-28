import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../hooks/usePanelApi";
import type { AppUpdateStatus } from "../types/panel";
import { getAppUpdateState, startAppUpdate, subscribeAppUpdate } from "../update/appUpdate";

interface UpdateAvailableModalProps {
  open: boolean;
  status: AppUpdateStatus;
  onDismiss: () => void;
}

export function UpdateAvailableModal({ open, status, onDismiss }: UpdateAvailableModalProps) {
  const [lockActive, setLockActive] = useState(() => getAppUpdateState().active);

  useEffect(() => subscribeAppUpdate((s) => setLockActive(s.active)), []);

  const handleDownload = () => {
    const api = getApi();
    if (api && typeof api.open_download_page === "function") {
      void api.open_download_page();
    }
  };

  const handleUpdateNow = () => {
    onDismiss();
    void startAppUpdate({
      localVersion: status.local_version,
      remoteVersion: status.remote_version,
    });
  };

  // Hide the toast while the full-app lock overlay owns the update UX.
  if (!open || lockActive) return null;

  const installed = status.channel === "installed" || status.installed;

  return createPortal(
    <div className="update-toast no-drag" role="status" aria-live="polite">
      <div className="update-toast-card">
        <button
          type="button"
          className="update-toast-close icon-btn"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        <div className="update-toast-header">
          <span className="update-toast-dot" aria-hidden />
          <span className="update-toast-title">{installed ? "Update ready" : "Update available"}</span>
          <span className="update-toast-version">v{status.remote_version}</span>
        </div>

        {installed ? (
          <p className="update-toast-body">
            <strong>v{status.local_version}</strong> → <strong>v{status.remote_version}</strong>
            {status.release_notes ? <> — {status.release_notes}</> : null}
          </p>
        ) : (
          <>
            <p className="update-toast-body">
              You&apos;re on <strong>v{status.local_version}</strong>. A newer build is ready to download.
            </p>
            <p className="update-toast-hint">
              Download it, run the new EXE, then delete the old one — your chats and settings stay in AppData.
            </p>
          </>
        )}

        <div className="update-toast-actions">
          <button type="button" className="update-toast-btn update-toast-btn-muted" onClick={onDismiss}>
            Later
          </button>
          {installed ? (
            <button
              type="button"
              className="update-toast-btn update-toast-btn-primary"
              onClick={handleUpdateNow}
            >
              Update now
            </button>
          ) : (
            <button type="button" className="update-toast-btn update-toast-btn-primary" onClick={handleDownload}>
              Download
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

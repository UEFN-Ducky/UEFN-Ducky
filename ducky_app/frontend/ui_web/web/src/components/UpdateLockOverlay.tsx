import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  cancelAppUpdate,
  getAppUpdateState,
  subscribeAppUpdate,
  type AppUpdateStage,
  type AppUpdateState,
} from "../update/appUpdate";

function formatBytes(n: number): string {
  if (!n || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function stageLabel(state: AppUpdateState): string {
  const { stage, downloadedBytes, totalBytes } = state;
  switch (stage as AppUpdateStage) {
    case "stopping_agents":
      return "Stopping agents…";
    case "check":
      return "Preparing update…";
    case "download": {
      if (totalBytes > 0) {
        const pct = Math.min(100, Math.round((downloadedBytes / totalBytes) * 100));
        return `Downloading update… ${pct}% (${formatBytes(downloadedBytes)} / ${formatBytes(totalBytes)})`;
      }
      return downloadedBytes > 0
        ? `Downloading update… ${formatBytes(downloadedBytes)}`
        : "Downloading update…";
    }
    case "verify":
      return "Verifying installer…";
    case "launch":
      return "Launching installer…";
    case "installing":
      return "Updating installer… waiting for Windows permission";
    case "restarting":
      return state.stuck
        ? "Install is taking too long — the app stayed open. Force close so Setup can finish."
        : "Installing update… UEFN Ducky will restart itself";
    case "error":
      return state.error || "Update failed.";
    default:
      return "Updating…";
  }
}

function downloadPercent(state: AppUpdateState): number | null {
  if (state.stage !== "download" || state.totalBytes <= 0) return null;
  return Math.min(100, Math.round((state.downloadedBytes / state.totalBytes) * 100));
}

function canCancel(state: AppUpdateState): boolean {
  const { stage } = state;
  if (stage === "error") return false;
  if (stage === "launch" || stage === "installing" || stage === "restarting") return state.stuck;
  return true;
}

export function UpdateLockOverlay() {
  const [state, setState] = useState<AppUpdateState>(() => getAppUpdateState());

  useEffect(() => subscribeAppUpdate(setState), []);

  useEffect(() => {
    if (!state.active) return;
    const block = (e: Event) => {
      // Let Escape cancel while the update is still abortable.
      if (
        e instanceof KeyboardEvent &&
        e.key === "Escape" &&
        canCancel(state)
      ) {
        e.preventDefault();
        e.stopPropagation();
        cancelAppUpdate();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
    };
    // Capture-phase so the rest of the app cannot receive input while locked.
    window.addEventListener("keydown", block, true);
    window.addEventListener("keyup", block, true);
    window.addEventListener("keypress", block, true);
    return () => {
      window.removeEventListener("keydown", block, true);
      window.removeEventListener("keyup", block, true);
      window.removeEventListener("keypress", block, true);
    };
  }, [state.active, state.stage, state.stuck]);

  if (!state.active) return null;

  const pct = downloadPercent(state);
  const local = state.localVersion ? `v${state.localVersion}` : null;
  const remote = state.remoteVersion ? `v${state.remoteVersion}` : null;
  const showCancel = canCancel(state);

  return createPortal(
    <div className="update-lock-overlay no-drag" role="alertdialog" aria-modal="true" aria-busy="true">
      <div className="update-lock-card">
        <img
          className="update-lock-logo"
          src="/OnlineMCPIcon.png"
          width={72}
          height={72}
          alt=""
          draggable={false}
        />
        <h2 className="update-lock-title">Updating UEFN Ducky</h2>
        {local && remote ? (
          <p className="update-lock-versions">
            {local} → <strong>{remote}</strong>
          </p>
        ) : remote ? (
          <p className="update-lock-versions">
            Installing <strong>{remote}</strong>
          </p>
        ) : null}
        <div className="update-lock-bar" aria-hidden>
          {pct != null ? (
            <div className="update-lock-bar-fill" style={{ width: `${pct}%` }} />
          ) : (
            <div className="update-lock-bar-indeterminate" />
          )}
        </div>
        <p className="update-lock-stage">{stageLabel(state)}</p>
        <p className="update-lock-hint">The app is locked while the update finishes.</p>
        {showCancel ? (
          <button
            type="button"
            className="update-lock-cancel"
            onClick={() => cancelAppUpdate()}
          >
            {state.stuck ? "Force close" : "Cancel"}
          </button>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

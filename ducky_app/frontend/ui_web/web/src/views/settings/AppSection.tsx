import { useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import type { AppUpdateStatus } from "../../types/panel";
import {
  getAppUpdateState,
  startAppUpdate,
  subscribeAppUpdate,
  type AppUpdateState,
} from "../../update/appUpdate";
import { useUiTarget } from "../../ui-targets/registry";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

type AppActionPhase = "idle" | "checking" | "uninstalling";
/** Result of the last manual update check. */
type CheckResult = "idle" | "latest" | "no_release" | "update" | "error";

function AppInfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z" />
      <path d="M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12" />
    </svg>
  );
}

function classifyCheck(next: AppUpdateStatus): CheckResult {
  if (next.error || next.feed_status === "error") return "error";
  if (next.update_available || next.feed_status === "update_available") return "update";
  if (next.feed_status === "no_release") return "no_release";
  if (next.feed_status === "up_to_date" || next.remote_version) return "latest";
  return "no_release";
}

function formatBytes(n: number): string {
  if (!n || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function updateProgressLabel(update: AppUpdateState): string {
  switch (update.stage) {
    case "stopping_agents":
      return "Stopping agents…";
    case "check":
      return "Preparing update…";
    case "download": {
      if (update.totalBytes > 0) {
        const pct = Math.min(100, Math.round((update.downloadedBytes / update.totalBytes) * 100));
        return `Downloading… ${pct}% (${formatBytes(update.downloadedBytes)} / ${formatBytes(update.totalBytes)})`;
      }
      return "Downloading update…";
    }
    case "verify":
      return "Verifying installer…";
    case "launch":
      return "Launching installer…";
    case "installing":
      return "Updating installer… waiting for Windows permission";
    case "restarting":
      return "Installing update… UEFN Ducky will restart itself";
    default:
      return "Updating…";
  }
}

export function AppSection() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [localVersionOnly, setLocalVersionOnly] = useState<string | null>(null);
  const [phase, setPhase] = useState<AppActionPhase>("idle");
  const [checkResult, setCheckResult] = useState<CheckResult>("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [updateState, setUpdateState] = useState<AppUpdateState>(() => getAppUpdateState());
  const { confirm } = useConfirmModal();

  useEffect(() => subscribeAppUpdate(setUpdateState), []);

  useEffect(() => {
    if (updateState.stage === "error" && updateState.error) {
      setErrorText(updateState.error);
    }
  }, [updateState.stage, updateState.error]);

  const refreshStatus = async () => {
    const api = getApi();
    if (!api || typeof api.get_app_update_status !== "function") return null;
    const next = await api.get_app_update_status();
    setStatus(next);
    return next;
  };

  /** Local version only — Store feed is checked when the user presses the button. */
  useEffect(() => {
    return onApiReady(() => {
      const api = getApi();
      if (!api || typeof api.get_version !== "function") return;
      void api
        .get_version()
        .then((v) => {
          if (typeof v === "string" && v.trim()) setLocalVersionOnly(v.trim());
        })
        .catch(() => {});
    });
  }, []);

  const handleUpdateNow = () => {
    if (!status) return;
    setErrorText(null);
    void startAppUpdate({
      localVersion: status.local_version,
      remoteVersion: status.remote_version,
    });
  };

  const handleCheckForUpdates = async () => {
    if (checkResult === "update" && status?.update_available) {
      handleUpdateNow();
      return;
    }

    setPhase("checking");
    setErrorText(null);
    try {
      const next = await refreshStatus();
      if (!next) {
        setCheckResult("error");
        setErrorText("Update check unavailable.");
        return;
      }
      const result = classifyCheck(next);
      setCheckResult(result);
      if (result === "error") {
        setErrorText(next.error || "Could not reach the update feed.");
      }
    } catch {
      setCheckResult("error");
      setErrorText("Could not reach the update feed.");
    } finally {
      setPhase("idle");
    }
  };

  const handleUninstall = async () => {
    const api = getApi();
    if (!api) return;
    const confirmed = await confirm({
      title: "Uninstall UEFN Ducky",
      message:
        "This removes the app from Windows. The uninstaller will then ask whether to also delete your chats, settings, and project data — choose No there to keep them for a future install.",
      confirmLabel: "Uninstall",
      danger: true,
    });
    if (!confirmed) return;
    setPhase("uninstalling");
    setErrorText(null);
    try {
      const result = await api.launch_uninstall();
      if (!result.ok) {
        setPhase("idle");
        setErrorText(result.error ?? `Uninstall failed (${result.stage}).`);
      }
    } catch {
      setPhase("idle");
      setErrorText("Uninstall failed to start.");
    }
  };

  const updating = updateState.active;
  const busy = phase !== "idle" || updating;
  const canSelfUpdate =
    !status ||
    status.channel === "installed" ||
    (Boolean(status.installed) && status.channel !== "dev");
  const localVersion = status?.local_version ?? localVersionOnly ?? "…";
  const remoteVersion = status?.remote_version;

  const updateBtnClass =
    checkResult === "latest"
      ? "settings-btn general-tab-btn-latest"
      : checkResult === "update"
        ? "settings-btn general-tab-btn-update"
        : "settings-btn";

  const updateBtnLabel =
    phase === "checking"
      ? "Checking…"
      : checkResult === "latest"
        ? "You're on the latest"
        : checkResult === "no_release"
          ? "No Store release yet"
          : checkResult === "update" && remoteVersion
            ? `Update to v${remoteVersion}`
            : checkResult === "update"
              ? "Update available"
              : checkResult === "error"
                ? "Check failed — retry"
                : "Check for updates";

  const downloadPct =
    updating && updateState.stage === "download" && updateState.totalBytes > 0
      ? Math.min(100, Math.round((updateState.downloadedBytes / updateState.totalBytes) * 100))
      : null;

  const versionLine = (() => {
    if (updating && remoteVersion) {
      return (
        <>
          v{localVersion} → <strong>v{remoteVersion}</strong>
        </>
      );
    }
    if (checkResult === "update" && remoteVersion) {
      return (
        <>
          v{localVersion} → <strong>v{remoteVersion}</strong> available
        </>
      );
    }
    if (checkResult === "latest") {
      return <>v{localVersion} — you&apos;re on the latest</>;
    }
    return <>v{localVersion}</>;
  })();

  const appTargetRef = useUiTarget("settings.general.app", {
    kind: "settings_field",
    label: "App Info",
    route: "settings.general",
  });

  return (
    <section className="general-tab-section" ref={appTargetRef}>
      <div className="general-tab-app-info">
        <div className="general-tab-app-info-copy">
          <GeneralSectionHeader
            icon={<AppInfoIcon />}
            title="App Info"
            description={<span className="general-tab-app-version">{versionLine}</span>}
          />
        </div>
        <div className="general-tab-app-info-actions">
          {canSelfUpdate && !updating ? (
            <div className="general-tab-btn-row">
              <button
                type="button"
                className={updateBtnClass}
                disabled={busy}
                onClick={() => void handleCheckForUpdates()}
              >
                {updateBtnLabel}
              </button>
              <button
                type="button"
                className="settings-btn"
                disabled={busy}
                onClick={() => void handleUninstall()}
              >
                {phase === "uninstalling" ? "Uninstalling…" : "Uninstall"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
      {updating ? (
        <div className="general-tab-update-progress">
          <div className="update-lock-bar" aria-hidden>
            {downloadPct != null ? (
              <div className="update-lock-bar-fill" style={{ width: `${downloadPct}%` }} />
            ) : (
              <div className="update-lock-bar-indeterminate" />
            )}
          </div>
          <p className="general-tab-update-progress-label">{updateProgressLabel(updateState)}</p>
        </div>
      ) : null}
      {errorText && !updating ? (
        <p className="general-tab-section-note general-tab-section-note--error">{errorText}</p>
      ) : null}
    </section>
  );
}

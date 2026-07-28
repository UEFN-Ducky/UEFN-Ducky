import { requestOpenSettings } from "../navigation/openSettingsTab";
import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { getApi } from "../hooks/usePanelApi";
import type { UpdaterResult, UpdateProgress } from "../types/panel";

export type AppUpdateStage =
  | "idle"
  | "stopping_agents"
  | "check"
  | "download"
  | "verify"
  | "launch"
  | "installing"
  | "restarting"
  | "error";

export interface AppUpdateState {
  active: boolean;
  stage: AppUpdateStage;
  downloadedBytes: number;
  totalBytes: number;
  error: string | null;
  localVersion: string | null;
  remoteVersion: string | null;
  /** True if we are still alive after Setup should have killed us — show Force close. */
  stuck: boolean;
}

const IDLE: AppUpdateState = {
  active: false,
  stage: "idle",
  downloadedBytes: 0,
  totalBytes: 0,
  error: null,
  localVersion: null,
  remoteVersion: null,
  stuck: false,
};

/** If the process is still here this long after "restarting", unlock Cancel. */
const RESTART_STUCK_MS = 12_000;

let state: AppUpdateState = { ...IDLE };
const listeners = new Set<(s: AppUpdateState) => void>();
let inFlight: Promise<void> | null = null;
let stuckTimer: ReturnType<typeof setTimeout> | null = null;

function clearStuckTimer(): void {
  if (stuckTimer != null) {
    clearTimeout(stuckTimer);
    stuckTimer = null;
  }
}

function publish(next: Partial<AppUpdateState>): void {
  state = { ...state, ...next };
  for (const fn of listeners) fn(state);
}

export function getAppUpdateState(): AppUpdateState {
  return state;
}

export function subscribeAppUpdate(fn: (s: AppUpdateState) => void): () => void {
  listeners.add(fn);
  fn(state);
  return () => listeners.delete(fn);
}

function mapStage(raw: string | undefined | null): AppUpdateStage {
  switch (raw) {
    case "stopping_agents":
    case "check":
    case "download":
    case "verify":
    case "launch":
    case "installing":
    case "restarting":
    case "error":
    case "idle":
      return raw;
    default:
      return state.stage === "idle" ? "check" : state.stage;
  }
}

async function pollProgress(signal: { stop: boolean }): Promise<void> {
  const api = getApi();
  if (!api || typeof api.get_update_progress !== "function") return;
  while (!signal.stop) {
    try {
      const prog: UpdateProgress = await api.get_update_progress();
      publish({
        stage: mapStage(prog.stage),
        downloadedBytes: Number(prog.downloaded_bytes) || 0,
        totalBytes: Number(prog.total_bytes) || 0,
        error: prog.error ?? state.error,
      });
    } catch {
      // ignore transient bridge blips while downloading
    }
    await new Promise((r) => setTimeout(r, 300));
  }
}

/**
 * Open Settings → General, lock the app, stop agents, and apply the update.
 * Safe to call from the toast or Apps Info — only one run at a time.
 */
export function startAppUpdate(opts?: {
  localVersion?: string | null;
  remoteVersion?: string | null;
}): Promise<void> {
  if (inFlight) return inFlight;

  requestOpenSettings("General");

  clearStuckTimer();
  publish({
    active: true,
    stage: "stopping_agents",
    downloadedBytes: 0,
    totalBytes: 0,
    error: null,
    stuck: false,
    localVersion: opts?.localVersion ?? state.localVersion,
    remoteVersion: opts?.remoteVersion ?? state.remoteVersion,
  });

  const signal = { stop: false };
  const poller = pollProgress(signal);

  inFlight = (async () => {
    const api = getApi();
    try {
      if (api && typeof api.cancel_agent === "function") {
        try {
          await api.cancel_agent("");
        } catch {
          // best-effort; backend apply_update also cancels
        }
      }

      const result = await runBridgeJob<UpdaterResult>("apply_update", [], 600_000);
      signal.stop = true;
      await poller.catch(() => {});

      if (result?.ok) {
        publish({
          active: true,
          stage: "restarting",
          error: null,
          stuck: false,
        });
        clearStuckTimer();
        // Backend should os._exit shortly; if we are still here, unlock Force close.
        stuckTimer = setTimeout(() => {
          if (state.active && state.stage === "restarting") {
            publish({ stuck: true });
          }
        }, RESTART_STUCK_MS);
        return;
      }

      const errMsg = result?.error ?? `Update failed (${result?.stage ?? "unknown"}).`;
      // Only the download cancel string — do not match "permission… try again" copy.
      if (/^update cancelled\.?$/i.test(errMsg.trim())) {
        clearStuckTimer();
        publish({ ...IDLE });
        return;
      }

      clearStuckTimer();
      publish({
        active: false,
        stage: "error",
        error: errMsg,
        stuck: false,
      });
    } catch (err) {
      signal.stop = true;
      await poller.catch(() => {});
      const msg = err instanceof Error ? err.message : "Update failed to start.";
      if (/^update cancelled\.?$/i.test(msg.trim())) {
        clearStuckTimer();
        publish({ ...IDLE });
        return;
      }
      clearStuckTimer();
      publish({
        active: false,
        stage: "error",
        error: msg,
        stuck: false,
      });
    } finally {
      inFlight = null;
    }
  })();

  return inFlight;
}

/**
 * Cancel an in-flight update (download/verify).
 * After Setup has launched, Cancel is a no-op unless the restart watchdog
 * marked us stuck — then Force close exits the stranded panel.
 */
export function cancelAppUpdate(): void {
  if (!state.active) return;
  if (state.stage === "launch" || state.stage === "installing" || state.stage === "restarting") {
    if (!state.stuck) return;
    clearStuckTimer();
    const api = getApi();
    if (api && typeof api.exit_all === "function") {
      void api.exit_all();
    }
    publish({ ...IDLE });
    return;
  }
  const api = getApi();
  if (api && typeof api.cancel_update === "function") {
    void api.cancel_update();
  }
}

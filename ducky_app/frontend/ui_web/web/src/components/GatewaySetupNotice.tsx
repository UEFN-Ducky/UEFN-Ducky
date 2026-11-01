/**
 * First-run setup (Welcome → starter plugins → connect an AI → defaults) and, for an install
 * that has no gateway outside the first run, the corner card that waits until one is installed.
 * Its pill can be hidden; the same reminder then stays at the top of Settings (SetupReminder).
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Icons } from "../icons/Icons";
import { onApiReady } from "../hooks/onApiReady";
import {
  beginStoreInstall,
  clearStoreJobLater,
  endStoreInstall,
  patchStoreJob,
} from "../hooks/storeInstallJobs";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { usageTrack } from "../hooks/usageTrack";
import type { StarterPluginDto } from "../types/panel";
import { reportFirstRunSetup } from "../walkthrough/firstRunSetup";
import { ensurePopularPlugins, markStarterPluginToursCompleted } from "../walkthrough/starterLlmGateways";
import { welcomeAfterSetup } from "../walkthrough/welcomeDucky";
import { markTourCompleted } from "../walkthrough/WalkthroughService";
import { SetupRatingModal } from "./SetupRatingModal";
import { SetupWizard, type InstallRow, type SetupStep } from "./SetupWizard";
import "./gateway-setup.css";

type SetupStatus = {
  pending_first_run?: boolean;
  gateway_ids?: string[];
  plugins?: StarterPluginDto[];
};

const PILL_HIDDEN_KEY = "ducky.setupPill.hidden";

function readPillHidden(): boolean {
  try {
    return localStorage.getItem(PILL_HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

// Shared with Settings: whether setup still waits on a gateway, and a way to open it.
let setupNeeded = false;
const setupSubs = new Set<() => void>();
let openSetupHandler: (() => void) | null = null;

function publishSetupNeeded(next: boolean): void {
  if (setupNeeded === next) return;
  setupNeeded = next;
  setupSubs.forEach((fn) => fn());
}

/** Open the setup that installs and connects an AI gateway (false when it is not mounted). */
export function openGatewaySetup(): boolean {
  if (!openSetupHandler) return false;
  openSetupHandler();
  return true;
}

export function useSetupNeeded(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      setupSubs.add(onChange);
      return () => setupSubs.delete(onChange);
    },
    () => setupNeeded,
    () => setupNeeded,
  );
}

/** "Finish setting up Ducky" at the top of Settings while no AI gateway is installed. */
export function SetupReminder() {
  const needed = useSetupNeeded();
  if (!needed) return null;
  return (
    <section className="gateway-setup-reminder" role="status">
      <div className="gateway-setup-reminder-text">
        <strong>Finish setting up Ducky</strong>
        <span>Chats need an AI plugin (Anthropic, OpenAI, Google or Cursor).</span>
      </div>
      <button
        type="button"
        className="update-toast-btn update-toast-btn-primary"
        onClick={() => openSetupHandler?.()}
      >
        Continue setup
      </button>
    </section>
  );
}

function reflectStoreProgress(
  slug: string,
  label: string,
  phase: string | undefined,
  detail: string | undefined,
): void {
  const base = { slug, name: label, label: `Downloading ${label}…` };
  if (phase === "installing") {
    beginStoreInstall(slug);
    patchStoreJob(slug, { ...base, phase: "working", step: "download" });
    return;
  }
  if (phase === "installed") {
    endStoreInstall(slug);
    clearStoreJobLater(slug, { ...base, label: `Installed ${label}`, phase: "done", step: "done" });
    return;
  }
  if (phase === "error") {
    endStoreInstall(slug);
    clearStoreJobLater(slug, {
      ...base,
      label: detail || `Couldn't install ${label}`,
      phase: "error",
      step: "done",
    });
    return;
  }
  if (phase === "skipped") {
    endStoreInstall(slug);
    patchStoreJob(slug, null);
  }
}

/** A plugin's row after one `starter_plugins_progress` event. */
export function rowForPhase(phase: string | undefined, detail?: string): InstallRow | null {
  if (phase === "installing") return { phase: "installing" };
  if (phase === "installed" || phase === "skipped") return { phase: "installed" };
  if (phase === "error") return { phase: "error", detail: detail || "Install failed" };
  return null;
}

/** Rows from the status: what is on disk is installed; a row still installing or failed keeps that. */
export function mergeRows(
  prev: Record<string, InstallRow>,
  plugins: StarterPluginDto[],
): Record<string, InstallRow> {
  const next: Record<string, InstallRow> = {};
  for (const p of plugins) {
    const old = prev[p.slug];
    if (p.installed) next[p.slug] = { phase: "installed" };
    else if (old && (old.phase === "installing" || old.phase === "error")) next[p.slug] = old;
    else next[p.slug] = { phase: "ready" };
  }
  return next;
}

export function GatewaySetupNotice() {
  const [ready, setReady] = useState(false);
  const [gatewayIds, setGatewayIds] = useState<string[]>([]);
  const [plugins, setPlugins] = useState<StarterPluginDto[]>([]);
  const [rows, setRows] = useState<Record<string, InstallRow>>({});
  // The step on screen, or null when the setup is closed.
  const [wizard, setWizard] = useState<SetupStep | null>(null);
  // After Finish: the 0–5 star rating of the setup, before the welcome ducky.
  const [rating, setRating] = useState(false);
  // After the rating: the welcome ducky is opening, so the app's own tours keep waiting.
  const [welcoming, setWelcoming] = useState(false);
  const [pill, setPill] = useState(false);
  const [pillHidden, setPillHidden] = useState(readPillHidden);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decided = useRef(false);
  // Anonymous funnel: which setup step is on screen (welcome → plugins → connect_ai → defaults → done).
  useEffect(() => {
    if (!wizard) return;
    usageTrack("setup.step", { step: wizard === "ai" ? "connect_ai" : wizard });
  }, [wizard]);

  const applyStatus = useCallback((status: SetupStatus | undefined) => {
    const gateways = status?.gateway_ids || [];
    const list = status?.plugins || [];
    setGatewayIds(gateways);
    setPlugins(list);
    setRows((prev) => mergeRows(prev, list));
    setReady(true);
    if (!decided.current) {
      decided.current = true;
      // A new install's setup, open until Finish (a restart part way through opens it again).
      const open = !!status?.pending_first_run;
      if (open) setWizard("welcome");
      reportFirstRunSetup(open);
    }
  }, []);

  const refresh = useCallback(() => {
    onApiReady((api) => {
      if (!api.starter_setup_status) {
        if (!decided.current) {
          decided.current = true;
          reportFirstRunSetup(false);
        }
        return;
      }
      void api.starter_setup_status().then(applyStatus).catch(() => {
        // Stay hidden when status can't be read; the Welcome tour must not wait on it.
        if (!decided.current) {
          decided.current = true;
          reportFirstRunSetup(false);
        }
      });
    });
  }, [applyStatus]);

  useEffect(() => {
    installPanelPushBus();
    refresh();
    return subscribePanelPush((event) => {
      if (event.type === "starter_plugins_progress") {
        const slug = event.slug || "";
        const label = event.label || slug;
        const row = rowForPhase(event.setup_phase, event.detail);
        if (slug && row) setRows((prev) => ({ ...prev, [slug]: row }));
        if (slug) reflectStoreProgress(slug, label, event.setup_phase, event.detail);
      }
      if (event.type === "uefn_plugins_changed" || event.type === "starter_plugins_progress") refresh();
    });
  }, [refresh]);

  useEffect(() => {
    publishSetupNeeded(ready && gatewayIds.length === 0);
  }, [ready, gatewayIds]);

  useEffect(() => {
    openSetupHandler = () => setWizard("plugins");
    return () => {
      openSetupHandler = null;
      publishSetupNeeded(false);
    };
  }, []);

  const hidePill = useCallback(() => {
    setPillHidden(true);
    try {
      localStorage.setItem(PILL_HIDDEN_KEY, "1");
    } catch {
      // A per-viewer convenience; the reminder in Settings stays either way.
    }
  }, []);

  // The app's own tours wait while the setup, or the welcome it hands over to, is on screen.
  useEffect(() => {
    if (decided.current) reportFirstRunSetup(wizard !== null || rating || welcoming);
  }, [wizard, rating, welcoming]);

  const install = useCallback(() => {
    setBusy(true);
    setError("");
    setRows((prev) =>
      Object.fromEntries(
        Object.entries(prev).map(([slug, row]) => [slug, row.phase === "error" ? { phase: "ready" } : row]),
      ) as Record<string, InstallRow>,
    );
    void ensurePopularPlugins(true)
      .then((result) => {
        // The job's own list of failures: a progress event can be missed (the event
        // stream reconnecting), and a failed plugin must not look ready to install.
        const failed = result?.errors || [];
        if (!failed.length) return;
        setRows((prev) => {
          const next = { ...prev };
          for (const f of failed) if (f.slug) next[f.slug] = { phase: "error", detail: f.error || "Install failed" };
          return next;
        });
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Download failed"))
      .finally(() => {
        setBusy(false);
        refresh();
      });
  }, [refresh]);

  const finish = useCallback(() => {
    usageTrack("setup.step", { step: "done" });
    // The setup covered the Store and LLM tours the Welcome tour would chain into.
    markStarterPluginToursCompleted();
    markTourCompleted("settings.store");
    markTourCompleted("llms.setup");
    setWizard(null);
    setRating(true);
  }, []);

  const rated = useCallback((stars: number) => {
    usageTrack("setup.rating", { stars: String(stars) });
    setRating(false);
    setWelcoming(true);
    void welcomeAfterSetup().finally(() => setWelcoming(false));
  }, []);

  if (!ready) return null;

  if (rating) return <SetupRatingModal onSubmit={rated} />;

  if (wizard) {
    return (
      <SetupWizard
        step={wizard}
        onStep={setWizard}
        plugins={plugins}
        rows={rows}
        installing={busy}
        installError={error}
        onInstall={install}
        onFinish={finish}
      />
    );
  }

  if (gatewayIds.length > 0 || pillHidden || welcoming) return null;

  if (pill) {
    return createPortal(
      <div className="gateway-setup gateway-setup--corner no-drag">
        <div className="gateway-setup-pill gateway-setup-pill--split">
          <button type="button" className="gateway-setup-pill-open" onClick={() => setPill(false)}>
            Set up Ducky
          </button>
          <button
            type="button"
            className="gateway-setup-pill-close"
            aria-label="Hide setup reminder"
            title="Hide — it stays at the top of Settings"
            onClick={hidePill}
          >
            <Icons.Close />
          </button>
        </div>
      </div>,
      document.body,
    );
  }

  return createPortal(
    <div className="gateway-setup gateway-setup--corner no-drag" role="status" aria-live="polite">
      <section className="gateway-setup-card">
        <header className="gateway-setup-head">
          <h2>Finish setting up Ducky</h2>
          <button type="button" className="gateway-setup-min" onClick={() => setPill(true)}>
            Minimize
          </button>
        </header>
        <p>
          Chats need an AI plugin (Anthropic, OpenAI, Google or Cursor). Setup installs it, then helps you
          connect it.
        </p>
        <button
          type="button"
          className="update-toast-btn update-toast-btn-primary gateway-setup-go"
          onClick={() => setWizard("plugins")}
        >
          Continue setup
        </button>
      </section>
    </div>,
    document.body,
  );
}

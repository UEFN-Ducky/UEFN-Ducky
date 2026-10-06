import { getApi } from "../hooks/usePanelApi";
import { pluginTourId } from "./pluginWalkthroughs";
import { markTourCompleted } from "./WalkthroughService";

export const STARTER_LLM_SLUGS = ["anthropic", "cursor", "openai"] as const;

let suppressStarterPluginTours = false;

export function setSuppressStarterPluginTours(value: boolean): void {
  suppressStarterPluginTours = value;
}

export function shouldSuppressPluginWalkthrough(pluginId: string): boolean {
  if (!suppressStarterPluginTours) return false;
  const id = pluginId.trim().toLowerCase().replace(/^plugin\./, "");
  return (STARTER_LLM_SLUGS as readonly string[]).includes(id);
}

export function markStarterPluginToursCompleted(): void {
  for (const slug of STARTER_LLM_SLUGS) {
    markTourCompleted(pluginTourId(slug));
  }
}

export function selectLlmsProvider(id: string | null): void {
  window.dispatchEvent(new CustomEvent("ducky:llms-select-provider", { detail: { id } }));
}

export async function peekStarterLlmOnboard(): Promise<{ pending: boolean }> {
  const api = getApi();
  if (!api?.starter_llm_onboard_pending) return { pending: false };
  try {
    const out = await api.starter_llm_onboard_pending();
    return { pending: !!out.pending };
  } catch {
    return { pending: false };
  }
}

/** What the bundle job reports: installed, already there, and per-plugin errors. */
export type StarterInstallResult = {
  ok?: boolean;
  installed?: string[];
  skipped?: string[];
  errors?: Array<{ slug: string; error: string; code?: string }>;
};

let popularInflight: Promise<StarterInstallResult> | null = null;

async function whenPanelApiReady(): Promise<void> {
  const { getApi } = await import("../hooks/usePanelApi");
  if (getApi()) return;
  const { onApiReady } = await import("../hooks/onApiReady");
  await new Promise<void>((resolve, reject) => {
    let stop = () => {};
    const timer = window.setTimeout(() => {
      stop();
      reject(new Error("Panel API unavailable"));
    }, 8000);
    stop = onApiReady(() => {
      window.clearTimeout(timer);
      resolve();
    });
  });
}

/** One bundle job. A second call waits for the job already running. */
export function ensurePopularPlugins(force = false): Promise<StarterInstallResult> {
  if (popularInflight) return popularInflight;
  popularInflight = (async () => {
    await whenPanelApiReady();
    const { runBridgeJob } = await import("../hooks/bridgeJobAsync");
    return runBridgeJob<StarterInstallResult>("ensure_starter_llm_gateways", [force], 1_200_000);
  })().finally(() => {
    popularInflight = null;
  });
  return popularInflight;
}

export async function ensureStarterLlmGateways(): Promise<void> {
  try {
    await ensurePopularPlugins(false);
  } catch {
    /* Caller can install from the setup card */
  }
}

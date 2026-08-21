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

export async function ensureStarterLlmGateways(): Promise<void> {
  try {
    sessionStorage.setItem("uefn-store-category", "gateways");
  } catch {
    /* ignore */
  }
  const { runBridgeJob } = await import("../hooks/bridgeJobAsync");
  try {
    await runBridgeJob("ensure_starter_llm_gateways", [], 240_000);
  } catch {
    /* Store step still continues — user can install manually */
  }
}

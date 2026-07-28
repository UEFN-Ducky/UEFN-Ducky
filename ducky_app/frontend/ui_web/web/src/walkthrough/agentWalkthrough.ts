/**
 * Ephemeral agent-authored tours — same coachmark UI as product walkthroughs,
 * not persisted to walkthrough_completed.
 */
import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { WalkthroughAdvance, WalkthroughSpotlightMode, WalkthroughStep } from "./types";
import {
  getWalkthroughState,
  registerTour,
  setWalkthroughFinishHook,
  skipTour,
  startTour,
  unregisterTour,
} from "./WalkthroughService";

export const AGENT_TOUR_ID = "agent.ephemeral";

function ensureFinishHook(): void {
  setWalkthroughFinishHook((tourId, reason) => {
    if (tourId !== AGENT_TOUR_ID) return;
    settleAgentWalkthrough(reason);
  });
}

export type AgentWalkthroughStepInput = {
  target: string;
  title?: string;
  body?: string;
  /** Alias for body. */
  label?: string;
  advance?: WalkthroughAdvance | string;
  mode?: WalkthroughSpotlightMode | string;
  /** Optional route to open before the step (same ids as ducky_ui_navigate). */
  navigate?: string;
};

const SETTINGS_TAB: Record<string, string> = {
  settings: "General",
  "settings.general": "General",
  "settings.store": "Store",
  "settings.llms": "LLMs",
  "settings.mcp": "LLMs",
  "settings.mcp_plugins": "LLMs",
  "settings.skills": "LLMs",
  "settings.appearance": "Appearance",
  "settings.duckies": "Duckies",
  "settings.plans": "Plans",
  "settings.memory": "LLMs",
  "settings.languages": "Languages",
  "settings.log_errors": "General",
  plans: "Plans",
};

const SETTINGS_SECTION: Record<string, string> = {
  "settings.general": "general",
  "settings.llms": "llms",
  "settings.mcp": "mcps",
  "settings.mcp_plugins": "mcps",
  "settings.skills": "skills",
  "settings.plans": "working",
  plans: "working",
  "settings.memory": "entries",
  "settings.log_errors": "errors",
};

function wait(ms: number): Promise<void> {
  return new Promise((r) => globalThis.setTimeout(r, ms));
}

function openRoute(route: string): void {
  const r = route.trim();
  const tab = SETTINGS_TAB[r];
  if (tab) {
    requestOpenSettings(tab);
    const section = SETTINGS_SECTION[r];
    if (section) {
      window.dispatchEvent(
        new CustomEvent("ducky:settings-section", { detail: { tab, section } }),
      );
    }
    return;
  }
  window.dispatchEvent(new CustomEvent("ducky:navigate", { detail: { route: r, item_id: "" } }));
}

type AgentFinish = { ok: true; completed: boolean; skipped: boolean; tour_id: string };

let pendingResolve: ((r: AgentFinish) => void) | null = null;

/** Called from WalkthroughService when an agent tour ends. */
export function settleAgentWalkthrough(reason: "complete" | "skip"): void {
  const resolve = pendingResolve;
  pendingResolve = null;
  if (resolve) {
    resolve({
      ok: true,
      completed: reason === "complete",
      skipped: reason === "skip",
      tour_id: AGENT_TOUR_ID,
    });
  }
}

export function parseAgentWalkthroughSteps(raw: unknown): WalkthroughStep[] {
  if (!Array.isArray(raw)) return [];
  const out: WalkthroughStep[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const s = row as AgentWalkthroughStepInput & { spotlight?: string };
    const target = String(s.target || s.spotlight || "").trim();
    if (!target) continue;
    const body = String(s.body || s.label || "").trim();
    const title = String(s.title || "").trim() || body.slice(0, 48) || target;
    const advance: WalkthroughAdvance = s.advance === "require_click" ? "require_click" : "next";
    const mode: WalkthroughSpotlightMode = s.mode === "circle" ? "circle" : "rect";
    const navigate = String(s.navigate || "").trim();
    out.push({
      target,
      title,
      body: body || title,
      advance,
      mode,
      onEnter: navigate
        ? async () => {
            openRoute(navigate);
            await wait(300);
          }
        : undefined,
    });
  }
  return out;
}

/**
 * Register + start an ephemeral coachmark tour. Resolves when the user finishes
 * or skips. Replaces any in-flight agent tour.
 */
export async function runAgentWalkthrough(rawSteps: unknown): Promise<Record<string, unknown>> {
  ensureFinishHook();
  const steps = parseAgentWalkthroughSteps(rawSteps);
  if (!steps.length) {
    return { error: "steps must be a non-empty list of {target, title, body}" };
  }

  if (pendingResolve) {
    settleAgentWalkthrough("skip");
  }
  if (getWalkthroughState().tourId === AGENT_TOUR_ID) {
    await skipTour();
  }

  unregisterTour(AGENT_TOUR_ID);
  registerTour({
    id: AGENT_TOUR_ID,
    title: "Guided tour",
    autoStart: "never",
    persist: false,
    steps,
  });

  return new Promise<Record<string, unknown>>((resolve) => {
    pendingResolve = (r) => resolve(r);
    void startTour(AGENT_TOUR_ID, { force: true }).then((ok) => {
      if (!ok) {
        pendingResolve = null;
        resolve({ error: "failed to start walkthrough" });
      }
    });
  });
}

/**
 * Store detail helpers: open a plugin's settings surface, redo its first-enable tour.
 */
import { getApi } from "../../../hooks/usePanelApi";
import { applySettingsHistory, type SettingsNavLocation } from "../../../navigation/settingsHistory";
import { requestOpenSettings } from "../../../navigation/openSettingsTab";
import type { SettingsTab } from "../../SettingsView";
import { pluginTourId, redoTour, getTour, registerTour } from "../../../walkthrough";
import { pluginManifestToTour, parsePluginWalkthroughs } from "../../../walkthrough/pluginWalkthroughs";

type Contributes = Record<string, unknown>;

export type PluginSettingsTarget =
  | { kind: "tab"; tab: string }
  | { kind: "llm"; providerId: string; label: string };

function asRows(raw: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(raw)) return [];
  return raw.filter((r): r is Record<string, unknown> => !!r && typeof r === "object");
}

/** Resolve where Settings → … should land for an installed plugin's contributes. */
export function settingsTargetFromContributes(contributes: Contributes | null | undefined): PluginSettingsTarget | null {
  if (!contributes || typeof contributes !== "object") return null;
  const tabs = asRows(contributes["settings.tabs"] ?? contributes.settings_tabs);
  const tabId = String(tabs[0]?.id || "").trim();
  if (tabId) return { kind: "tab", tab: tabId };

  const providers = asRows(contributes["llm.providers"] ?? contributes.llm_providers);
  const providerId = String(providers[0]?.id || "").trim();
  if (providerId) {
    return {
      kind: "llm",
      providerId,
      label: String(providers[0]?.label || providerId).trim() || providerId,
    };
  }

  const wt = contributes.walkthrough;
  if (wt && typeof wt === "object") {
    const settingsTab = String((wt as Record<string, unknown>).settings_tab || "").trim();
    if (settingsTab) return { kind: "tab", tab: settingsTab };
  }
  return null;
}

export function pluginHasWalkthrough(contributes: Contributes | null | undefined): boolean {
  const wt = contributes?.walkthrough;
  if (!wt || typeof wt !== "object") return false;
  const steps = (wt as Record<string, unknown>).steps;
  return Array.isArray(steps) && steps.length > 0;
}

/** Load contributes for an installed plugin id (works even when the plugin is disabled). */
export async function loadPluginContributes(pluginId: string): Promise<Contributes | null> {
  const id = pluginId.trim().toLowerCase();
  if (!id) return null;
  const api = getApi();
  if (!api?.list_uefn_plugins) return null;
  try {
    const res = await api.list_uefn_plugins();
    const row = (res.plugins || []).find((p) => (p.id || "").toLowerCase() === id);
    const contrib = (row as { contributes?: Contributes } | undefined)?.contributes;
    return contrib && typeof contrib === "object" ? contrib : null;
  } catch {
    return null;
  }
}

/** Open the Settings surface this plugin owns (tab or LLMs provider detail). */
export function openPluginSettingsTarget(target: PluginSettingsTarget): void {
  if (target.kind === "tab") {
    requestOpenSettings(target.tab as SettingsTab);
    return;
  }
  const loc: SettingsNavLocation = {
    kind: "settings",
    tab: "LLMs",
    sectionTab: "llms",
    drill: { type: "llms", providerId: target.providerId, usage: false },
    name: target.label,
  };
  requestOpenSettings("LLMs");
  window.dispatchEvent(
    new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section: "llms" } }),
  );
  // SettingsView may mount on this tick — history applier queues if not ready yet.
  queueMicrotask(() => applySettingsHistory(loc));
}

export async function openInstalledPluginSettings(pluginId: string): Promise<boolean> {
  const contributes = await loadPluginContributes(pluginId);
  const target = settingsTargetFromContributes(contributes);
  if (!target) return false;
  openPluginSettingsTarget(target);
  return true;
}

export async function redoInstalledPluginWalkthrough(pluginId: string): Promise<boolean> {
  const tourId = pluginTourId(pluginId);
  if (!getTour(tourId)) {
    const contributes = await loadPluginContributes(pluginId);
    if (!pluginHasWalkthrough(contributes)) return false;
    const rows = parsePluginWalkthroughs([
      {
        ...(contributes!.walkthrough as Record<string, unknown>),
        plugin_id: pluginId,
      },
    ]);
    const tour = rows[0] ? pluginManifestToTour(rows[0]) : null;
    if (!tour) return false;
    registerTour(tour);
  }
  return redoTour(tourId);
}

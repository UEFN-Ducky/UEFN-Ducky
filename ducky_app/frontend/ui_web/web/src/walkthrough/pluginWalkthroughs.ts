import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { PluginWalkthroughManifest, WalkthroughDef } from "./types";

function wait(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

/** Normalize a contributes.walkthrough row into a host WalkthroughDef. */
export function pluginManifestToTour(raw: PluginWalkthroughManifest): WalkthroughDef | null {
  const id = String(raw.id || raw.plugin_id || "").trim().toLowerCase();
  if (!id || !Array.isArray(raw.steps) || raw.steps.length === 0) return null;
  const tourId = id.startsWith("plugin.") ? id : `plugin.${id}`;
  const settingsTab = (raw.settings_tab || "").trim();
  const steps = raw.steps
    .filter((s) => s && typeof s.target === "string" && s.target.trim())
    .map((s, i) => ({
      target: s.target.trim(),
      title: String(s.title || "Tip").trim() || "Tip",
      body: String(s.body || "").trim(),
      advance: s.advance === "require_click" ? ("require_click" as const) : ("next" as const),
      mode: s.mode === "circle" ? ("circle" as const) : ("rect" as const),
      onEnter:
        i === 0 && settingsTab
          ? async () => {
              requestOpenSettings(settingsTab);
              await wait(350);
            }
          : undefined,
    }));
  if (!steps.length) return null;
  return {
    id: tourId,
    title: raw.title,
    autoStart: "never",
    steps,
  };
}

export function parsePluginWalkthroughs(rows: unknown): PluginWalkthroughManifest[] {
  if (!Array.isArray(rows)) return [];
  const out: PluginWalkthroughManifest[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const r = row as Record<string, unknown>;
    const id = String(r.id || r.plugin_id || "").trim();
    if (!id || !Array.isArray(r.steps)) continue;
    out.push({
      id,
      title: typeof r.title === "string" ? r.title : undefined,
      auto_start: r.auto_start === "never" ? "never" : "first_enable",
      settings_tab: typeof r.settings_tab === "string" ? r.settings_tab : undefined,
      steps: r.steps as PluginWalkthroughManifest["steps"],
      plugin_id: typeof r.plugin_id === "string" ? r.plugin_id : id,
    });
  }
  return out;
}

/** Tour id for a plugin id (`translation` → `plugin.translation`). */
export function pluginTourId(pluginId: string): string {
  const id = pluginId.trim().toLowerCase();
  return id.startsWith("plugin.") ? id : `plugin.${id}`;
}

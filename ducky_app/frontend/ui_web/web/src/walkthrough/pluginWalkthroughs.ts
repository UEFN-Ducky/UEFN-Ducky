import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { PluginWalkthroughManifest, WalkthroughDef } from "./types";

function selectLlmsProvider(id: string | null): void {
  window.dispatchEvent(new CustomEvent("ducky:llms-select-provider", { detail: { id } }));
}

function wait(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

const LLM_DETAIL_TARGETS = new Set([
  "settings.llms.provider.key",
  "settings.llms.provider.save",
  "settings.llms.provider.key_section",
  "settings.llms.provider.ide",
  "settings.llms.provider.ide.apply",
  "settings.llms.provider.agent",
  "settings.llms.provider.agent.detect",
  "settings.llms.provider.plugin",
  "settings.llms.back",
]);

const PROVIDER_ROW_RE = /^settings\.llms\.provider\.([a-z0-9_-]+)$/i;
const RESERVED_PROVIDER_IDS = new Set([
  "key",
  "save",
  "agent",
  "ide",
  "plugin",
  "back",
  "key_section",
]);

export function isLlmsProviderRowTarget(target: string): boolean {
  const t = target.trim();
  const m = PROVIDER_ROW_RE.exec(t);
  if (!m) return false;
  return !RESERVED_PROVIDER_IDS.has(m[1].toLowerCase());
}

/** Provider row id for a gateway tour (google → gemini when the row target says so). */
export function inferLlmsWalkthroughProviderId(raw: PluginWalkthroughManifest): string {
  const explicit = String(raw.provider_id || "").trim().toLowerCase();
  if (explicit) return explicit;
  for (const s of raw.steps || []) {
    const t = String(s?.target || "").trim();
    const m = PROVIDER_ROW_RE.exec(t);
    if (m && !RESERVED_PROVIDER_IDS.has(m[1].toLowerCase())) return m[1].toLowerCase();
  }
  return String(raw.plugin_id || raw.id || "")
    .trim()
    .toLowerCase()
    .replace(/^plugin\./, "");
}

async function enterLlmsWalkthroughStep(
  target: string,
  settingsTab: string,
  providerId: string,
): Promise<void> {
  if (settingsTab) requestOpenSettings(settingsTab);
  const llms =
    settingsTab === "LLMs" || target.startsWith("settings.llms") || target === "settings.llms.providers";
  if (llms) {
    window.dispatchEvent(
      new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section: "llms" } }),
    );
    if (
      isLlmsProviderRowTarget(target) ||
      target === "settings.llms.providers" ||
      target === "settings.llms.section.llms"
    ) {
      selectLlmsProvider(null);
      await wait(280);
      return;
    }
    if (LLM_DETAIL_TARGETS.has(target) && providerId) {
      selectLlmsProvider(providerId);
      await wait(450);
      return;
    }
  }
  if (settingsTab) await wait(350);
}

/** Normalize a contributes.walkthrough row into a host WalkthroughDef. */
export function pluginManifestToTour(raw: PluginWalkthroughManifest): WalkthroughDef | null {
  const id = String(raw.id || raw.plugin_id || "").trim().toLowerCase();
  if (!id || !Array.isArray(raw.steps) || raw.steps.length === 0) return null;
  const tourId = id.startsWith("plugin.") ? id : `plugin.${id}`;
  const settingsTab = (raw.settings_tab || "").trim();
  const providerId = inferLlmsWalkthroughProviderId(raw);
  const steps = raw.steps
    .filter((s) => s && typeof s.target === "string" && s.target.trim())
    .map((s, i) => {
      const target = s.target.trim();
      const llmsStep = target.startsWith("settings.llms") || settingsTab === "LLMs";
      return {
        target,
        title: String(s.title || "Tip").trim() || "Tip",
        body: String(s.body || "").trim(),
        advance: s.advance === "require_click" ? ("require_click" as const) : ("next" as const),
        mode: s.mode === "circle" ? ("circle" as const) : ("rect" as const),
        onEnter:
          llmsStep || (i === 0 && settingsTab)
            ? () => enterLlmsWalkthroughStep(target, settingsTab, providerId)
            : undefined,
      };
    });
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
      provider_id: typeof r.provider_id === "string" ? r.provider_id : undefined,
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

import { requestOpenSettings } from "../navigation/openSettingsTab";
import { getTour, listTourIds } from "./WalkthroughService";
import type { PluginWalkthroughManifest, WalkthroughDef } from "./types";

/** Slice of plugin contributions needed to fill gateway tour gaps. */
export type GatewayWalkthroughContrib = {
  ide_hookups?: Array<{ plugin_id?: string }>;
  llm_coding_agents?: Array<{ plugin_id?: string }>;
  settings_sections?: Array<{ tab?: string; plugin_id?: string; title?: string; description?: string }>;
};

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

function pluginKey(raw: PluginWalkthroughManifest): string {
  return String(raw.plugin_id || raw.id || "")
    .trim()
    .toLowerCase()
    .replace(/^plugin\./, "");
}

function samePlugin(id: string | undefined, pluginId: string): boolean {
  return String(id || "")
    .trim()
    .toLowerCase()
    .replace(/^plugin\./, "") === pluginId;
}

function findStep(
  steps: PluginWalkthroughManifest["steps"],
  pred: (target: string) => boolean,
): PluginWalkthroughManifest["steps"][number] | undefined {
  return steps.find((s) => pred(String(s?.target || "").trim()));
}

/**
 * Gateway tours must cover every fillable slide section that plugin actually has
 * (key, Test & Save, IDE, coding agent, plugin settings, Back).
 */
export function expandGatewayManifest(
  raw: PluginWalkthroughManifest,
  contrib: GatewayWalkthroughContrib = {},
): PluginWalkthroughManifest {
  if (!raw.steps?.some((s) => isLlmsProviderRowTarget(String(s?.target || "")))) return raw;
  const pid = pluginKey(raw);
  const hasIde = (contrib.ide_hookups || []).some((h) => samePlugin(h.plugin_id, pid));
  const hasAgent = (contrib.llm_coding_agents || []).some((a) => samePlugin(a.plugin_id, pid));
  const pluginSection = (contrib.settings_sections || []).find(
    (s) => samePlugin(s.plugin_id, pid) && String(s.tab || "").toLowerCase() === "llms",
  );
  const row = findStep(raw.steps, isLlmsProviderRowTarget);
  const label = String(row?.title || raw.title || pid).replace(/^Open\s+/i, "").trim() || pid;
  const pick = (target: string) => findStep(raw.steps, (t) => t === target);
  const step = (
    target: string,
    title: string,
    body: string,
    advance: "next" | "require_click" = "next",
  ) => {
    const existing = pick(target);
    return {
      target,
      title: String(existing?.title || title),
      body: String(existing?.body || body),
      advance: existing?.advance === "require_click" || advance === "require_click" ? ("require_click" as const) : ("next" as const),
      mode: existing?.mode === "circle" ? ("circle" as const) : ("rect" as const),
    };
  };

  const steps: PluginWalkthroughManifest["steps"] = [];
  if (row) {
    steps.push({
      ...row,
      advance: "require_click",
      mode: row.mode === "circle" ? "circle" : "rect",
    });
  }
  steps.push(
    step(
      "settings.llms.provider.key",
      `${label} key`,
      `Paste a key (or URL) here. This is what Ducky uses for this gateway.`,
    ),
    step(
      "settings.llms.provider.save",
      "Test & Save",
      "Press Test & Save after you fill the field. No key? Press Skip.",
      "require_click",
    ),
  );
  if (hasIde) {
    steps.push(
      step(
        "settings.llms.provider.ide",
        "IDE / MCP connection",
        "IDE / MCP wires UEFN tools and Ducky skills into this editor. Apply once, then Test.",
      ),
      step(
        "settings.llms.provider.ide.apply",
        "Apply connection",
        "Press Apply (or Re-apply) so UEFN MCP and Ducky skills land in this IDE. Then Test — a green check means connected.",
      ),
    );
  }
  if (hasAgent) {
    steps.push(
      step(
        "settings.llms.provider.agent",
        "Coding agent",
        "This is the coding agent for this gateway. Detect finds the CLI. Keep the toggle on to pick it in chat.",
      ),
      step(
        "settings.llms.provider.agent.detect",
        "Detect CLI",
        "Press Detect to find the CLI on this machine. Leave the toggle on so this agent appears in the chat picker.",
      ),
    );
  }
  if (pluginSection) {
    steps.push(
      step(
        "settings.llms.provider.plugin",
        pluginSection.title || `${label} settings`,
        pluginSection.description || "Gateway-specific options for this provider — turn them on if you want them.",
      ),
    );
  }
  steps.push(
    step("settings.llms.back", "Back", "Press Back to return to the provider list.", "require_click"),
  );
  return { ...raw, steps };
}

/** Registered plugin tours that open an LLMs provider row (enabled gateways). */
export function listEnabledGatewayTours(): WalkthroughDef[] {
  const out: WalkthroughDef[] = [];
  for (const id of listTourIds()) {
    if (!id.startsWith("plugin.")) continue;
    const tour = getTour(id);
    if (!tour?.steps.some((s) => isLlmsProviderRowTarget(s.target))) continue;
    out.push(tour);
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
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

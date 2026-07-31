import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "./onApiReady";
import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

export type PluginSettingsTab = {
  id: string;
  label?: string;
  icon?: string;
  ui?: string;
  plugin_id?: string;
};

export type PluginDockPanel = {
  id: string;
  title?: string;
  defaultSide?: string;
  ui?: string;
  css?: string;
  plugin_id?: string;
};

export type PluginEditorKind = {
  kind: string;
  title?: string;
  /** Phase-2 panel reference, e.g. ``panel:asset``. */
  ui?: string;
  plugin_id?: string;
  /** Optional exact extensions (``.uasset``, ``.fbx``). When set, match these over ``kind``. */
  suffixes?: string[];
};

export type PluginHeaderButton = {
  id: string;
  title?: string;
  icon?: string;
  /** Builtin action id, e.g. `builtin:open-discord` (wired in pluginHeaderActions.ts). */
  action: string;
  order?: number;
  plugin_id?: string;
};

export type PluginUiPanel = {
  id: string;
  title?: string;
  icon?: string;
  entry: string;
  plugin_id?: string;
  version?: number;
};

export type PluginSettingsProperty = {
  id: string;
  /** "boolean" (default) | "secret" | "string" | "select". */
  type?: string;
  default?: boolean | string | number;
  label?: string;
  description?: string;
  placeholder?: string;
  /** Choices for a "select" property. */
  options?: Array<{ value: string; label?: string }>;
  /** Show a Test button for secret fields (plugin registers api.register_secret_test). */
  testable?: boolean;
};

export type PluginSettingsSection = {
  id: string;
  tab: string;
  title?: string;
  description?: string;
  order?: number;
  properties: PluginSettingsProperty[];
  plugin_id?: string;
};

export type PluginShellBoot = {
  plugin_id: string;
  entry: string;
};

export type PluginAppearanceProfile = {
  id: string;
  name: string;
  plugin_id: string;
  foundation?: Record<string, string>;
  overrides?: Record<string, string>;
  status_overrides?: Record<string, Partial<Record<"bg" | "border" | "text" | "dim", string>>>;
};

export type PluginAppearanceCss = {
  plugin_id: string;
  entry: string;
};

export type PluginAppearanceEffect = {
  id: string;
  label: string;
  plugin_id: string;
  entry: string;
};

export type PluginAppearanceSkin = {
  id: string;
  label: string;
  plugin_id: string;
  entry: string;
  css?: string;
};

export type PluginSound = {
  id: string;
  label?: string;
  file: string;
  plugin_id: string;
};

export type PluginHook = {
  id: string;
  label?: string;
  plugin_id: string;
};

/** Verse New-file scaffold from `contributes.verse.templates`. */
export type PluginVerseTemplate = {
  id: string;
  name: string;
  icon: string;
  description?: string;
  content: string;
  order?: number;
  file?: string;
  folder?: string;
  files?: Array<{ path: string; content: string }>;
  connects?: string[];
  plugin_id: string;
};

export type PluginTtsVoice = {
  id: string;
  label?: string;
  plugin_id: string;
};

/** LLM gateway row contributed into Settings → LLMs → Providers. */
export type PluginLlmProvider = {
  id: string;
  label: string;
  kind: "secret" | "url";
  secret_key: string;
  default_url?: string;
  order?: number;
  plugin_id: string;
  shows_thinking_effort?: boolean;
};

export type PluginContributions = {
  settings_tabs: PluginSettingsTab[];
  settings_sections: PluginSettingsSection[];
  dock_panels: PluginDockPanel[];
  editor_kinds: PluginEditorKind[];
  header_buttons: PluginHeaderButton[];
  ui_panels: PluginUiPanel[];
  shell_boots: PluginShellBoot[];
  appearance_profiles: PluginAppearanceProfile[];
  appearance_css: PluginAppearanceCss[];
  appearance_effects: PluginAppearanceEffect[];
  appearance_skins: PluginAppearanceSkin[];
  sounds: PluginSound[];
  hooks: PluginHook[];
  verse_templates: PluginVerseTemplate[];
  tts_voices: PluginTtsVoice[];
  /** Enabled plugin ids that expose dynamic (runtime-fetched) TTS voices. */
  tts_voice_plugins: string[];
  /** Enabled gateway providers (OpenAI, Ollama, …) shown under LLMs → Providers. */
  llm_providers: PluginLlmProvider[];
  /** Enabled gateway coding agents (e.g. Codex via OpenAI plugin). */
  llm_coding_agents: Array<{
    id: string;
    label: string;
    order?: number;
    plugin_id: string;
    shows_thinking_effort?: boolean;
  }>;
  /** IDE MCP/skills Apply targets owned by enabled gateway plugins. */
  ide_hookups: Array<{ kind: string; label?: string; plugin_id: string }>;
  /** Product walkthroughs from `contributes.walkthrough`. */
  walkthroughs: Array<{
    id: string;
    title?: string;
    auto_start?: string;
    settings_tab?: string;
    steps: Array<{
      target: string;
      title: string;
      body: string;
      advance?: string;
      mode?: string;
    }>;
    plugin_id?: string;
  }>;
  enabled_ids: string[];
  /** True after the first contributions fetch finishes (success or empty). */
  ready: boolean;
};

const EMPTY: PluginContributions = {
  settings_tabs: [],
  settings_sections: [],
  dock_panels: [],
  editor_kinds: [],
  header_buttons: [],
  ui_panels: [],
  shell_boots: [],
  appearance_profiles: [],
  appearance_css: [],
  appearance_effects: [],
  appearance_skins: [],
  sounds: [],
  hooks: [],
  verse_templates: [],
  tts_voices: [],
  tts_voice_plugins: [],
  llm_providers: [],
  llm_coding_agents: [],
  ide_hookups: [],
  walkthroughs: [],
  enabled_ids: [],
  ready: false,
};

/** Live contribution registry from enabled UEFN plugins (Store / local). */
export function usePluginContributions(): PluginContributions {
  const [contrib, setContrib] = useState<PluginContributions>(EMPTY);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api || typeof api.get_uefn_plugin_contributions !== "function") {
      setContrib((prev) => (prev.ready ? prev : { ...prev, ready: true }));
      return;
    }
    try {
      const next = await api.get_uefn_plugin_contributions();
      // Failed fetch: leave ready=false so AppearanceContext does not wipe a
      // saved plugin theme against an empty enabled_ids list.
      if (!next || next.ok === false) return;
      const settingsTabs = Array.isArray(next.settings_tabs) ? next.settings_tabs : [];
      // Skip no-op refreshes — uefn_plugins_changed used to rebuild the Plugins
      // sidebar on every identical payload (felt like a constant reload).
      const nextWalkthroughs = Array.isArray((next as { walkthroughs?: unknown }).walkthroughs)
        ? (
            (next as { walkthroughs: PluginContributions["walkthroughs"] }).walkthroughs || []
          ).filter(
            (w) =>
              !!w && typeof w.id === "string" && Array.isArray(w.steps) && w.steps.length > 0,
          )
        : [];
      setContrib((prev) => {
        if (
          prev.ready &&
          prev.settings_tabs.length === settingsTabs.length &&
          prev.settings_tabs.every(
            (t, i) =>
              t.id === settingsTabs[i]?.id &&
              t.plugin_id === settingsTabs[i]?.plugin_id &&
              t.label === settingsTabs[i]?.label,
          ) &&
          prev.enabled_ids.length === (Array.isArray(next.enabled_ids) ? next.enabled_ids.length : 0) &&
          prev.enabled_ids.every(
            (id, i) => id === (Array.isArray(next.enabled_ids) ? next.enabled_ids[i] : undefined),
          ) &&
          prev.walkthroughs.length === nextWalkthroughs.length &&
          prev.walkthroughs.every(
            (w, i) =>
              w.id === nextWalkthroughs[i]?.id &&
              w.plugin_id === nextWalkthroughs[i]?.plugin_id &&
              w.steps.length === nextWalkthroughs[i]?.steps.length,
          )
        ) {
          return prev;
        }
        return {
        settings_tabs: settingsTabs,
        settings_sections: Array.isArray(next.settings_sections)
          ? next.settings_sections
              .filter(
                (s): s is PluginSettingsSection =>
                  !!s &&
                  typeof s.id === "string" &&
                  typeof s.tab === "string" &&
                  Array.isArray(s.properties),
              )
              .map((s) => ({
                ...s,
                properties: s.properties.filter((p) => p && typeof p.id === "string"),
              }))
          : [],
        dock_panels: Array.isArray(next.dock_panels) ? next.dock_panels : [],
        editor_kinds: Array.isArray(next.editor_kinds) ? next.editor_kinds : [],
        header_buttons: Array.isArray(next.header_buttons)
          ? next.header_buttons.filter(
              (b): b is PluginHeaderButton =>
                !!b && typeof b.id === "string" && typeof b.action === "string",
            )
          : [],
        ui_panels: Array.isArray(next.ui_panels)
          ? next.ui_panels.filter(
              (p): p is PluginUiPanel =>
                !!p &&
                typeof p.id === "string" &&
                typeof p.entry === "string" &&
                typeof p.plugin_id === "string",
            )
          : [],
        shell_boots: Array.isArray((next as { shell_boots?: unknown }).shell_boots)
          ? ((next as { shell_boots: PluginShellBoot[] }).shell_boots || []).filter(
              (b): b is PluginShellBoot =>
                !!b && typeof b.plugin_id === "string" && typeof b.entry === "string",
            )
          : [],
        appearance_profiles: Array.isArray(
          (next as { appearance_profiles?: unknown }).appearance_profiles,
        )
          ? (
              (next as { appearance_profiles: PluginAppearanceProfile[] }).appearance_profiles || []
            ).filter(
              (p): p is PluginAppearanceProfile =>
                !!p &&
                typeof p.id === "string" &&
                typeof p.name === "string" &&
                typeof p.plugin_id === "string",
            )
          : [],
        appearance_css: Array.isArray((next as { appearance_css?: unknown }).appearance_css)
          ? ((next as { appearance_css: PluginAppearanceCss[] }).appearance_css || []).filter(
              (c): c is PluginAppearanceCss =>
                !!c && typeof c.plugin_id === "string" && typeof c.entry === "string",
            )
          : [],
        appearance_effects: Array.isArray(
          (next as { appearance_effects?: unknown }).appearance_effects,
        )
          ? (
              (next as { appearance_effects: PluginAppearanceEffect[] }).appearance_effects || []
            ).filter(
              (e): e is PluginAppearanceEffect =>
                !!e &&
                typeof e.id === "string" &&
                typeof e.label === "string" &&
                typeof e.plugin_id === "string" &&
                typeof e.entry === "string",
            )
          : [],
        appearance_skins: Array.isArray((next as { appearance_skins?: unknown }).appearance_skins)
          ? ((next as { appearance_skins: PluginAppearanceSkin[] }).appearance_skins || []).filter(
              (s): s is PluginAppearanceSkin =>
                !!s &&
                typeof s.id === "string" &&
                typeof s.label === "string" &&
                typeof s.plugin_id === "string" &&
                typeof s.entry === "string",
            )
          : [],
        sounds: Array.isArray((next as { sounds?: unknown }).sounds)
          ? ((next as { sounds: PluginSound[] }).sounds || []).filter(
              (s): s is PluginSound =>
                !!s &&
                typeof s.id === "string" &&
                typeof s.file === "string" &&
                typeof s.plugin_id === "string",
            )
          : [],
        hooks: Array.isArray((next as { hooks?: unknown }).hooks)
          ? ((next as { hooks: PluginHook[] }).hooks || []).filter(
              (h): h is PluginHook =>
                !!h && typeof h.id === "string" && typeof h.plugin_id === "string",
            )
          : [],
        verse_templates: Array.isArray((next as { verse_templates?: unknown }).verse_templates)
          ? ((next as { verse_templates: PluginVerseTemplate[] }).verse_templates || [])
              .filter(
                (t): t is PluginVerseTemplate =>
                  !!t &&
                  typeof t.id === "string" &&
                  typeof t.name === "string" &&
                  typeof t.icon === "string" &&
                  typeof t.content === "string" &&
                  typeof t.plugin_id === "string",
              )
              .map((t) => ({
                ...t,
                files: Array.isArray(t.files)
                  ? t.files.filter(
                      (f): f is { path: string; content: string } =>
                        !!f && typeof f.path === "string" && typeof f.content === "string",
                    )
                  : undefined,
                connects: Array.isArray(t.connects)
                  ? t.connects.filter((c): c is string => typeof c === "string" && c.length > 0)
                  : undefined,
              }))
              .slice()
              .sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.name.localeCompare(b.name))
          : [],
        tts_voices: Array.isArray((next as { tts_voices?: unknown }).tts_voices)
          ? ((next as { tts_voices: PluginTtsVoice[] }).tts_voices || []).filter(
              (v): v is PluginTtsVoice =>
                !!v && typeof v.id === "string" && typeof v.plugin_id === "string",
            )
          : [],
        tts_voice_plugins: Array.isArray((next as { tts_voice_plugins?: unknown }).tts_voice_plugins)
          ? ((next as { tts_voice_plugins: string[] }).tts_voice_plugins || []).filter(
              (p): p is string => typeof p === "string" && p.length > 0,
            )
          : [],
        llm_providers: Array.isArray((next as { llm_providers?: unknown }).llm_providers)
          ? ((next as { llm_providers: PluginLlmProvider[] }).llm_providers || [])
              .filter(
                (p): p is PluginLlmProvider =>
                  !!p &&
                  typeof p.id === "string" &&
                  typeof p.label === "string" &&
                  typeof p.plugin_id === "string" &&
                  (p.kind === "secret" || p.kind === "url") &&
                  typeof p.secret_key === "string",
              )
              .slice()
              .sort((a, b) => (a.order ?? 100) - (b.order ?? 100))
          : [],
        llm_coding_agents: Array.isArray((next as { llm_coding_agents?: unknown }).llm_coding_agents)
          ? (
              (next as {
                llm_coding_agents: Array<{
                  id: string;
                  label: string;
                  order?: number;
                  plugin_id: string;
                  shows_thinking_effort?: boolean;
                }>;
              }).llm_coding_agents || []
            )
              .filter(
                (a) =>
                  !!a &&
                  typeof a.id === "string" &&
                  typeof a.label === "string" &&
                  typeof a.plugin_id === "string",
              )
              .slice()
              .sort((a, b) => (a.order ?? 100) - (b.order ?? 100))
          : [],
        ide_hookups: Array.isArray((next as { ide_hookups?: unknown }).ide_hookups)
          ? (
              (next as {
                ide_hookups: Array<{ kind: string; label?: string; plugin_id: string }>;
              }).ide_hookups || []
            ).filter(
              (h) =>
                !!h &&
                typeof h.kind === "string" &&
                typeof h.plugin_id === "string",
            )
          : [],
        walkthroughs: nextWalkthroughs,
        enabled_ids: Array.isArray(next.enabled_ids) ? next.enabled_ids : [],
        ready: true,
        };
      });
    } catch {
      /* leave ready false — retry on next uefn_plugins_changed / remount */
    }
  }, []);

  useEffect(() => {
    return onApiReady(() => {
      void refresh();
    });
  }, [refresh]);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "uefn_plugins_changed") void refresh();
    });
  }, [refresh]);

  // Missed uefn_plugins_changed (push before bus / no window) left Installed empty
  // forever — poll until the first successful contributions snapshot lands.
  useEffect(() => {
    if (contrib.ready) return;
    const id = window.setInterval(() => {
      void refresh();
    }, 400);
    return () => window.clearInterval(id);
  }, [contrib.ready, refresh]);

  return contrib;
}

export function pluginContributesSettingsTab(contrib: PluginContributions, tabId: string): boolean {
  return contrib.settings_tabs.some((t) => t.id === tabId);
}

export function pluginContributesDockPanel(contrib: PluginContributions, panelId: string): boolean {
  return contrib.dock_panels.some((p) => p.id === panelId);
}

/** Resolve ``ui: "panel:<panelId>"`` on a dock contribution (Phase-2 plugin HTML). */
export function dockPanelPluginUi(
  contrib: PluginContributions,
  dockPanelId: string,
): { pluginId: string; uiPanelId: string } | null {
  const row = contrib.dock_panels.find((p) => p.id === dockPanelId);
  if (!row) return null;
  const ui = String(row.ui || "").trim();
  if (!ui.startsWith("panel:")) return null;
  const uiPanelId = ui.slice("panel:".length).trim().toLowerCase();
  const pluginId = String(row.plugin_id || "").trim().toLowerCase();
  if (!uiPanelId || !pluginId) return null;
  return { pluginId, uiPanelId };
}

export function pluginContributesEditorKind(contrib: PluginContributions, kind: string): boolean {
  return contrib.editor_kinds.some((k) => k.kind === kind);
}

/** Resolve a plugin UI panel that claims this file path (suffixes first, then kind). */
export function resolvePluginEditorForFile(
  contrib: PluginContributions,
  relativePath: string,
  fileKind: string,
): { pluginId: string; panelId: string } | null {
  const lower = (relativePath || "").toLowerCase().replace(/\\/g, "/");
  const dot = lower.lastIndexOf(".");
  const suffix = dot >= 0 ? lower.slice(dot) : "";

  let byKind: PluginEditorKind | undefined;
  for (const row of contrib.editor_kinds) {
    const pluginId = String(row.plugin_id || "").trim().toLowerCase();
    const ui = String(row.ui || "").trim();
    if (!pluginId || !ui.startsWith("panel:")) continue;
    const suffixes = Array.isArray(row.suffixes)
      ? row.suffixes.map((s) => String(s || "").toLowerCase())
      : [];
    if (suffix && suffixes.includes(suffix)) {
      return { pluginId, panelId: ui.slice("panel:".length).trim().toLowerCase() };
    }
    if (!byKind && row.kind === fileKind) {
      byKind = row;
    }
  }
  if (!byKind) return null;
  const pluginId = String(byKind.plugin_id || "").trim().toLowerCase();
  const ui = String(byKind.ui || "").trim();
  if (!pluginId || !ui.startsWith("panel:")) return null;
  return { pluginId, panelId: ui.slice("panel:".length).trim().toLowerCase() };
}

export function pluginContributesHeaderButton(contrib: PluginContributions, buttonId: string): boolean {
  return contrib.header_buttons.some((b) => b.id === buttonId);
}

export function pluginSettingsSectionsForTab(
  contrib: PluginContributions,
  tabId: string,
): PluginSettingsSection[] {
  return contrib.settings_sections
    .filter((s) => s.tab === tabId)
    .sort((a, b) => {
      const ao = typeof a.order === "number" ? a.order : 100;
      const bo = typeof b.order === "number" ? b.order : 100;
      if (ao !== bo) return ao - bo;
      return String(a.id).localeCompare(String(b.id));
    });
}

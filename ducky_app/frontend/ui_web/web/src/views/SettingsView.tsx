import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import {
  readLastSettingsSections,
  readLastSettingsTab,
  registerSettingsTabConsumer,
  rememberSettingsSections,
  rememberSettingsTab,
} from "../navigation/openSettingsTab";
import {
  publishSettingsDrillApply,
  registerSettingsHistoryApplier,
  type SettingsNavLocation,
} from "../navigation/settingsHistory";
import { useRecordSettingsLocation } from "../navigation/useSettingsHistory";
import { CtrlWheelZoomRoot } from "../components/CtrlWheelZoomRoot";
import { SplitResizeHandle } from "../components/SplitResizeHandle";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import {
  usePluginContributions,
  type PluginSettingsTab,
} from "../hooks/usePluginContributions";
import { burstConfettiFromElement } from "../utils/confettiBurst";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { useAppearance } from "../theme/AppearanceContext";
import { AccountTab } from "./settings/AccountTab";
import { AddToUefnTab } from "./settings/AddToUefnTab";
import { AgentTab } from "./settings/AgentTab";
import { DiscordTab, type DiscordSectionTab } from "./settings/DiscordTab";
import { LanguagesTab } from "./settings/LanguagesTab";
import { DuckiesTab } from "./settings/DuckiesTab";
import { PlansTab, type PlansSectionTab } from "./settings/PlansTab";
import { MemoryTab, type MemorySectionTab } from "./settings/MemoryTab";
import { LogErrorsTab, type LogErrorsSectionTab } from "./settings/LogErrorsTab";
import { AppearanceTab } from "./settings/AppearanceTab";
import { AppearanceProfileHeader } from "./settings/AppearanceProfileBar";
import { AudioTab, type AudioSectionTab } from "./settings/AudioTab";
import { SkillsMcpTab } from "./settings/SkillsMcpTab";
import { SourceControlTab } from "./settings/SourceControlTab";
import { StoreTab } from "./settings/StoreTab";
import { SupportTab } from "./settings/SupportTab";
import { PluginSettingsSections } from "./settings/PluginSettingsSections";
import { INSTALLED_CATEGORY } from "./settings/storeFilters";
import {
  countWorkingStoreJobs,
  formatStoreJobBadge,
  useStoreInstallJobs,
} from "../hooks/storeInstallJobs";
import { useStoreUpdateBadge } from "../hooks/useStoreUpdateBadge";
import { PluginSettingsEmbed } from "../plugin-ui/PluginSettingsEmbed";
import { PANEL_ACTION_PREFIX } from "../plugin-ui/constants";
import { resolvePluginHeaderIcon } from "../hooks/pluginHeaderActions";
import { CONNECTION_ICONS } from "../connectionIcons";
import { useSettingsSidebarWidth } from "./settings/useSettingsSidebarWidth";
import { settingsTabTargetId, targetRef, useUiTarget } from "../ui-targets/registry";

/** Re-enable when URC / urc.exe wiring is ready. */
const SOURCE_CONTROL_TAB_ENABLED = false;

/** Host-owned Settings tabs (not gated by plugins). */
const CORE_TABS = [
  "Store",
  "General",
  "Duckies",
  "Plans",
  "LLMs",
  "Source Control",
  "Appearance",
  "Audio",
] as const;

export type SettingsTab = string;

/** Header sections under Settings → LLMs. */
export type LlmsSectionTab = "llms" | "skills" | "mcps" | "memory";

/** Header sections under Settings → General. */
export type GeneralSectionTab = "general" | "log_errors";

/** Color asset or emoji — line SVGs read as monochrome in the Settings rail. */
const CORE_TAB_ICONS: Record<(typeof CORE_TABS)[number], string> = {
  Store: "🛒",
  General: "⚙️",
  Duckies: CONNECTION_ICONS.online,
  Plans: "📋",
  LLMs: "🧠",
  "Source Control": "🌿",
  Appearance: "🎨",
  Audio: "🔊",
};

/** Sidebar label — Store tab is the Plugins page (catalog + installed). */
function coreTabLabel(tab: (typeof CORE_TABS)[number]): string {
  return tab === "Store" ? "Plugins" : tab;
}

/** Host React forms still wired via builtin: ui ids from plugins. */
const BUILTIN_SETTINGS_UI: Record<string, () => JSX.Element> = {
  "builtin:account-settings": () => <AccountTab />,
  "builtin:languages-settings": () => <LanguagesTab />,
};

const LLMS_SECTION_TABS: { id: LlmsSectionTab; label: string }[] = [
  { id: "llms", label: "LLMs" },
  { id: "skills", label: "Skills" },
  { id: "mcps", label: "MCPs" },
  { id: "memory", label: "Memory" },
];

const GENERAL_SECTION_TABS: { id: GeneralSectionTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "log_errors", label: "Log & Errors" },
];

const LOG_ERRORS_SECTION_TABS: { id: LogErrorsSectionTab; label: string }[] = [
  { id: "log", label: "Log" },
  { id: "errors", label: "Errors" },
];

const PLANS_SECTION_TABS: { id: PlansSectionTab; label: string }[] = [
  { id: "templates", label: "Templates" },
  { id: "working", label: "Project Plans" },
];

const DISCORD_SECTION_TABS: { id: DiscordSectionTab; label: string }[] = [
  { id: "bots", label: "Bots" },
  { id: "commands", label: "Commands" },
];

function readInitialLlmsSection(): LlmsSectionTab {
  const s = readLastSettingsSections();
  if (s.llms === "skills" || s.llms === "mcps" || s.llms === "memory" || s.llms === "llms") {
    return s.llms;
  }
  // Former LLMs → Plans nesting; Plans is its own sidebar tab now.
  if (s.llms === "plans") return "llms";
  // Migrate pre-nest skillsMcp persistence.
  if (s.skillsMcp === "mcps") return "mcps";
  if (s.skillsMcp === "skills") return "skills";
  // Migrated from former sidebar "Memory" tab (raw key before normalize).
  try {
    const raw = (localStorage.getItem("uefn-panel-settings-active-tab") || "").trim();
    if (raw === "Memory") return "memory";
  } catch {
    /* ignore */
  }
  return "llms";
}

function readInitialGeneralSection(): GeneralSectionTab {
  const s = readLastSettingsSections();
  if (s.general === "log_errors") return "log_errors";
  // Migrated from former sidebar "Log & Errors" tab (raw key before normalize).
  try {
    const raw = (localStorage.getItem("uefn-panel-settings-active-tab") || "").trim();
    if (raw === "Log & Errors") return "log_errors";
  } catch {
    /* ignore */
  }
  return "general";
}

/** Apply a leaf section id onto LLMs header + Memory sub-row state. */
function applyLlmsLeafSection(
  section: string,
  setLlms: (v: LlmsSectionTab) => void,
  setMemory: (v: MemorySectionTab) => void,
): boolean {
  if (section === "llms" || section === "skills" || section === "mcps" || section === "memory") {
    setLlms(section);
    return true;
  }
  if (section === "entries" || section === "context") {
    setLlms("memory");
    setMemory(section);
    return true;
  }
  return false;
}

/** Apply a leaf section id onto Plans header (Templates | Project Plans). */
function applyPlansLeafSection(
  section: string,
  setPlans: (v: PlansSectionTab) => void,
): boolean {
  if (section === "templates" || section === "working") {
    setPlans(section);
    return true;
  }
  return false;
}

/** Apply a leaf section id onto General header + Log/Errors sub-row state. */
function applyGeneralLeafSection(
  section: string,
  setGeneral: (v: GeneralSectionTab) => void,
  setLogErrors: (v: LogErrorsSectionTab) => void,
): boolean {
  if (section === "general" || section === "log_errors") {
    setGeneral(section);
    return true;
  }
  if (section === "log" || section === "errors") {
    setGeneral("log_errors");
    setLogErrors(section);
    return true;
  }
  return false;
}

const MEMORY_SECTION_TABS: { id: MemorySectionTab; label: string }[] = [
  { id: "entries", label: "Entries" },
  { id: "context", label: "Context" },
];

const AUDIO_SECTION_TABS: { id: AudioSectionTab; label: string }[] = [
  { id: "input", label: "Input" },
  { id: "output", label: "Output" },
  { id: "voice", label: "AI Voice" },
];

function parsePanelUi(ui: string | undefined): { pluginIdHint?: string; panelId: string } | null {
  const raw = (ui || "").trim();
  if (!raw.startsWith(PANEL_ACTION_PREFIX)) return null;
  const panelId = raw.slice(PANEL_ACTION_PREFIX.length).trim().toLowerCase();
  return panelId ? { panelId } : null;
}

const PLUGINS_NAV_OPEN_KEY = "uefn-panel-settings-plugins-nav-open";

function readPluginsNavOpen(): boolean {
  try {
    const raw = localStorage.getItem(PLUGINS_NAV_OPEN_KEY);
    if (raw === "0" || raw === "false") return false;
    if (raw === "1" || raw === "true") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function rememberPluginsNavOpen(open: boolean): void {
  try {
    localStorage.setItem(PLUGINS_NAV_OPEN_KEY, open ? "1" : "0");
  } catch {
    /* ignore */
  }
}

interface SettingsViewProps {
  version?: string;
}

export function SettingsView({ version }: SettingsViewProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(
    () => readLastSettingsTab() || "General",
  );
  const [llmsSection, setLlmsSection] = useState<LlmsSectionTab>(readInitialLlmsSection);
  const [generalSection, setGeneralSection] = useState<GeneralSectionTab>(readInitialGeneralSection);
  const [logErrorsSection, setLogErrorsSection] = useState<LogErrorsSectionTab>(() => {
    const s = readLastSettingsSections().logErrors;
    return s === "errors" ? "errors" : "log";
  });
  const [plansSection, setPlansSection] = useState<PlansSectionTab>(() => {
    const s = readLastSettingsSections().plans;
    return s === "templates" ? "templates" : "working";
  });
  const [memorySection, setMemorySection] = useState<MemorySectionTab>(() => {
    const s = readLastSettingsSections().memory;
    return s === "context" ? "context" : "entries";
  });
  const [audioSection, setAudioSection] = useState<AudioSectionTab>(() => {
    const s = readLastSettingsSections().audio;
    if (s === "output" || s === "voice") return s;
    return "input";
  });
  const [discordSection, setDiscordSection] = useState<DiscordSectionTab>(() => {
    const s = readLastSettingsSections().discord;
    return s === "commands" ? "commands" : "bots";
  });
  const [resolvedVersion, setResolvedVersion] = useState(version ?? "…");
  const { guardUnsavedChanges } = useAppearance();
  const sidebarScopeClass = useScopedClass("settings-view-sidebar-shell");
  const {
    width: sidebarWidth,
    iconsOnly: sidebarIconsOnly,
    onResize: onSidebarResize,
    persistWidth: persistSidebarWidth,
  } = useSettingsSidebarWidth();
  const pluginContrib = usePluginContributions();
  const { hasUpdates: hasStoreUpdates } = useStoreUpdateBadge();
  const { jobs: storeJobs } = useStoreInstallJobs();
  const storeJobCount = useMemo(() => countWorkingStoreJobs(storeJobs), [storeJobs]);
  const storeJobBadge = formatStoreJobBadge(storeJobCount);
  const [pluginsNavOpen, setPluginsNavOpen] = useState(readPluginsNavOpen);
  /** Store detail slug — highlights the matching row under Plugins. */
  const [storePluginFocus, setStorePluginFocus] = useState<string | null>(null);
  /** Keep Store mounted after first visit so Update All Pending survives tab switches. */
  const [storeMounted, setStoreMounted] = useState(() => activeTab === "Store");
  useEffect(() => {
    if (activeTab === "Store") setStoreMounted(true);
  }, [activeTab]);

  useEffect(() => {
    const onFocus = (e: Event) => {
      const slug = (e as CustomEvent<{ slug?: string | null }>).detail?.slug;
      setStorePluginFocus(typeof slug === "string" && slug.trim() ? slug.trim().toLowerCase() : null);
    };
    window.addEventListener("ducky:store-focus", onFocus);
    return () => window.removeEventListener("ducky:store-focus", onFocus);
  }, []);

  const pluginTabs = pluginContrib.settings_tabs;
  const activeIsPluginTab = pluginTabs.some((t) => t.id === activeTab);
  const storeIsPluginsPage = activeTab === "Store";
  // Unique installed plugins that contribute a settings tab (matches the accordion list).
  const installedPluginCount = useMemo(() => {
    const ids = new Set(
      pluginTabs.map((t) => (t.plugin_id || t.id || "").trim().toLowerCase()).filter(Boolean),
    );
    return ids.size;
  }, [pluginTabs]);
  const installedPluginCountLabel =
    installedPluginCount > 9 ? "9+" : String(installedPluginCount);

  const visibleCoreTabs = useMemo(() => {
    return CORE_TABS.filter((tab) => {
      if (!SOURCE_CONTROL_TAB_ENABLED && tab === "Source Control") return false;
      return true;
    });
  }, []);

  const showHeaderSubTabs =
    activeTab === "LLMs" ||
    activeTab === "Plans" ||
    activeTab === "General" ||
    activeTab === "Audio" ||
    activeTab === "Discord";
  const showAppearanceProfileHeader = activeTab === "Appearance";

  const activePluginTab: PluginSettingsTab | undefined = useMemo(
    () => pluginTabs.find((t) => t.id === activeTab),
    [pluginTabs, activeTab],
  );

  // Open the Plugins accordion when landing on a plugin tab or the Plugins (Store) page.
  // Depend on activeTab only — including pluginsNavOpen re-opens it the moment the user collapses.
  useEffect(() => {
    if (!activeIsPluginTab && !storeIsPluginsPage) return;
    setPluginsNavOpen(true);
    rememberPluginsNavOpen(true);
  }, [activeTab, activeIsPluginTab, storeIsPluginsPage]);

  const openPluginsPage = () => {
    setPluginsNavOpen(true);
    rememberPluginsNavOpen(true);
    try {
      sessionStorage.setItem("uefn-store-category", INSTALLED_CATEGORY);
    } catch {
      /* ignore */
    }
    void (async () => {
      if (activeTab !== "Store") {
        if (!(await guardUnsavedChanges())) return;
        setActiveTab("Store");
        rememberSettingsTab("Store");
      }
      window.dispatchEvent(new CustomEvent("ducky:store-navigate"));
    })();
  };

  const openPluginInStore = (pluginId: string) => {
    const slug = pluginId.trim().toLowerCase();
    if (!slug) {
      openPluginsPage();
      return;
    }
    setPluginsNavOpen(true);
    rememberPluginsNavOpen(true);
    try {
      sessionStorage.setItem(
        "uefn-store-install",
        JSON.stringify({ slug, install: false }),
      );
    } catch {
      /* ignore */
    }
    void (async () => {
      if (activeTab !== "Store") {
        if (!(await guardUnsavedChanges())) return;
        setActiveTab("Store");
        rememberSettingsTab("Store");
      }
      window.dispatchEvent(new CustomEvent("ducky:store-install"));
    })();
  };

  const onPluginsHeaderClick = () => {
    // Always open the Plugins page (Installed). Expand the list so rows stay clickable.
    setPluginsNavOpen(true);
    rememberPluginsNavOpen(true);
    openPluginsPage();
  };

  const onPluginsChevronClick = (e: MouseEvent) => {
    e.stopPropagation();
    setPluginsNavOpen((prev) => {
      const next = !prev;
      rememberPluginsNavOpen(next);
      return next;
    });
  };

  useEffect(() => {
    // Wait for plugin contributions — bouncing "Discord"/etc to Store while
    // settings_tabs is still empty made Manage bots open Store + thrashed the sidebar.
    if (!pluginContrib.ready) return;
    if (pluginTabs.some((t) => t.id === activeTab)) return;
    if (activeTab === "Skills & MCP" || activeTab === "Memory") {
      if (activeTab === "Memory") setLlmsSection("memory");
      setActiveTab("LLMs");
      rememberSettingsTab("LLMs");
      return;
    }
    if (activeTab === "Log & Errors") {
      setActiveTab("General");
      setGeneralSection("log_errors");
      rememberSettingsTab("General");
      return;
    }
    if ((CORE_TABS as readonly string[]).includes(activeTab) || activeTab === "Support") return;
    // Host builtin plugin tabs (ui id wired in BUILTIN_SETTINGS_UI) — keep while
    // contrib list catches up after a remount / deep-link.
    if (activeTab === "Discord" || activeTab === "Account" || activeTab === "Languages") return;
    setActiveTab("Store");
    rememberSettingsTab("Store");
  }, [activeTab, pluginTabs, pluginContrib.ready]);

  useEffect(() => {
    if (version) {
      setResolvedVersion(version);
      return;
    }
    let cancelled = false;
    void getApi()
      ?.get_version()
      .then((v) => {
        if (!cancelled && typeof v === "string" && v.trim()) setResolvedVersion(v);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [version]);

  const requestTab = async (tab: SettingsTab) => {
    if (tab === activeTab) return;
    if (!(await guardUnsavedChanges())) return;
    setActiveTab(tab);
    rememberSettingsTab(tab);
  };

  useEffect(() => {
    rememberSettingsTab(activeTab);
  }, [activeTab]);

  useEffect(() => {
    rememberSettingsSections({
      llms: llmsSection,
      general: generalSection,
      logErrors: logErrorsSection,
      plans: plansSection,
      memory: memorySection,
      audio: audioSection,
      discord: discordSection,
    });
  }, [
    llmsSection,
    generalSection,
    logErrorsSection,
    plansSection,
    memorySection,
    audioSection,
    discordSection,
  ]);

  /** Leaf section for history: llms|skills|mcps|templates|working|entries|context|… */
  const sectionTab = useMemo(() => {
    if (activeTab === "LLMs") {
      if (llmsSection === "memory") return memorySection;
      return llmsSection;
    }
    if (activeTab === "Plans") return plansSection;
    if (activeTab === "General") {
      return generalSection === "log_errors" ? logErrorsSection : generalSection;
    }
    if (activeTab === "Audio") return audioSection;
    if (activeTab === "Discord") return discordSection;
    return undefined;
  }, [
    activeTab,
    llmsSection,
    plansSection,
    generalSection,
    logErrorsSection,
    memorySection,
    audioSection,
    discordSection,
  ]);

  // Tabs with master/detail slides record themselves (incl. root) so a tab-only
  // record here can't clobber a drill entry after back/forward apply.
  const drillOwnedTab =
    activeTab === "Store" ||
    activeTab === "Duckies" ||
    activeTab === "Plans" ||
    activeTab === "LLMs" ||
    (activeTab === "Discord" && discordSection === "bots");
  const settingsNavLoc = useMemo<SettingsNavLocation | null>(() => {
    if (drillOwnedTab) return null;
    return {
      kind: "settings",
      tab: activeTab,
      sectionTab,
      name: sectionTab ? `${activeTab} · ${sectionTab}` : activeTab,
    };
  }, [activeTab, sectionTab, drillOwnedTab]);
  useRecordSettingsLocation(settingsNavLoc);

  const applyHistoryLoc = useCallback(
    async (loc: SettingsNavLocation) => {
      // Old history stored Plans under LLMs (sectionTab templates|working).
      const nestedPlans =
        loc.tab === "LLMs" &&
        (loc.sectionTab === "templates" || loc.sectionTab === "working" || loc.drill?.type === "plans");
      const tab =
        loc.tab === "Skills & MCP" || loc.tab === "Memory"
          ? "LLMs"
          : loc.tab === "Log & Errors"
            ? "General"
            : nestedPlans
              ? "Plans"
              : loc.tab;
      if (tab !== activeTab) {
        if (!(await guardUnsavedChanges())) return;
        setActiveTab(tab);
        rememberSettingsTab(tab);
      }
      if (tab === "Plans") {
        if (loc.sectionTab === "templates" || loc.sectionTab === "working") {
          setPlansSection(loc.sectionTab);
        } else if (loc.drill?.type === "plans") {
          setPlansSection("working");
        }
      } else if (tab === "LLMs" || loc.tab === "Skills & MCP" || loc.tab === "Memory") {
        if (loc.tab === "Skills & MCP" && (loc.sectionTab === "skills" || loc.sectionTab === "mcps")) {
          setLlmsSection(loc.sectionTab);
        } else if (loc.tab === "Memory" && (loc.sectionTab === "entries" || loc.sectionTab === "context")) {
          setLlmsSection("memory");
          setMemorySection(loc.sectionTab);
        } else if (loc.tab === "Memory") {
          setLlmsSection("memory");
        } else if (loc.sectionTab) {
          applyLlmsLeafSection(loc.sectionTab, setLlmsSection, setMemorySection);
        }
      } else if (tab === "General" || loc.tab === "Log & Errors") {
        if (loc.sectionTab) {
          applyGeneralLeafSection(loc.sectionTab, setGeneralSection, setLogErrorsSection);
        } else if (loc.tab === "Log & Errors") {
          setGeneralSection("log_errors");
        }
      } else if (
        loc.tab === "Audio" &&
        (loc.sectionTab === "input" || loc.sectionTab === "output" || loc.sectionTab === "voice")
      ) {
        setAudioSection(loc.sectionTab);
      } else if (loc.tab === "Discord") {
        if (loc.sectionTab === "commands" || loc.sectionTab === "bots") {
          setDiscordSection(loc.sectionTab);
        } else if (loc.drill?.type === "discord") {
          setDiscordSection("bots");
        }
      }
      // Child tabs may mount on this tick — publish after paint.
      const publishLoc: SettingsNavLocation =
        tab === loc.tab ? loc : { ...loc, tab, name: loc.name };
      queueMicrotask(() => publishSettingsDrillApply(publishLoc));
    },
    [activeTab, guardUnsavedChanges],
  );

  const applyHistoryLocRef = useRef(applyHistoryLoc);
  applyHistoryLocRef.current = applyHistoryLoc;

  useEffect(() => {
    return registerSettingsHistoryApplier((loc) => {
      void applyHistoryLocRef.current(loc);
    });
  }, []);

  const requestTabRef = useRef(requestTab);
  requestTabRef.current = requestTab;

  useEffect(() => {
    return registerSettingsTabConsumer((tab) => {
      void requestTabRef.current(tab);
    });
  }, []);

  // Guided-UI navigate can deep-link into a tab's inner section.
  // SettingsView owns that section state, so apply it here.
  useEffect(() => {
    const onSection = (event: Event) => {
      const detail = (event as CustomEvent).detail as { tab?: string; section?: string } | undefined;
      if (!detail?.section) return;
      if (detail.tab === "Plans") {
        applyPlansLeafSection(detail.section, setPlansSection);
        return;
      }
      if (detail.tab === "LLMs" || detail.tab === "Skills & MCP" || detail.tab === "Memory") {
        applyLlmsLeafSection(detail.section, setLlmsSection, setMemorySection);
        return;
      }
      if (detail.tab === "General" || detail.tab === "Log & Errors") {
        applyGeneralLeafSection(detail.section, setGeneralSection, setLogErrorsSection);
        return;
      }
      if (
        detail.tab === "Audio" &&
        (detail.section === "input" || detail.section === "output" || detail.section === "voice")
      ) {
        setAudioSection(detail.section as AudioSectionTab);
        return;
      }
      if (
        detail.tab === "Discord" &&
        (detail.section === "bots" || detail.section === "commands")
      ) {
        setDiscordSection(detail.section);
      }
    };
    window.addEventListener("ducky:settings-section", onSection);
    return () => window.removeEventListener("ducky:settings-section", onSection);
  }, []);

  const handleSupportClick = async (event: MouseEvent<HTMLButtonElement>) => {
    const button = event.currentTarget;
    if (!(await guardUnsavedChanges())) return;
    burstConfettiFromElement(button);
    setActiveTab("Support");
    rememberSettingsTab("Support");
  };

  const contentTargetRef = useUiTarget("settings.content", {
    kind: "settings_field",
    label: "Settings content",
    route: "settings",
  });

  const renderPluginTabBody = (tab: PluginSettingsTab) => {
    const ui = (tab.ui || "").trim();
    const Builtin = BUILTIN_SETTINGS_UI[ui];
    if (Builtin) return <Builtin />;
    const panel = parsePanelUi(ui);
    if (panel && tab.plugin_id) {
      return (
        <div className="general-tab-shell plugin-settings-tab-shell">
          <PluginSettingsSections tabId={tab.id} />
          <PluginSettingsEmbed pluginId={tab.plugin_id} panelId={panel.panelId} />
        </div>
      );
    }
    return (
      <div className="general-tab-shell">
        <PluginSettingsSections tabId={tab.id} />
      </div>
    );
  };

  return (
    <div className="settings-view no-drag">
      <div className="settings-view-body no-drag">
        <ScopedCss
          selector={`.${sidebarScopeClass}`}
          rules={{ "--settings-sidebar-width": `${sidebarWidth}px` }}
        />
        <div
          className={`settings-view-sidebar-shell ${sidebarScopeClass}${sidebarIconsOnly ? " is-icons-only" : ""}`}
        >
          <CtrlWheelZoomRoot className="settings-view-sidebar" storageKey="uefn-panel-settings-sidebar-zoom">
            <nav className="settings-view-sidebar-nav" aria-label="Configuration">
              <button
                type="button"
                className={`settings-view-sidebar-tab settings-view-sidebar-tab--support${activeTab === "Support" ? " is-active" : ""}`}
                onClick={handleSupportClick}
                title={sidebarIconsOnly ? "Support" : undefined}
              >
                <Icons.Patreon />
                <span>Support</span>
              </button>
              {visibleCoreTabs.map((tab) => {
                const tabEmoji = CORE_TAB_ICONS[tab];
                const label = coreTabLabel(tab);
                const showUpdateDot = tab === "Store" && hasStoreUpdates;
                const showJobBadge = tab === "Store" && Boolean(storeJobBadge);
                return (
                  <button
                    key={tab}
                    ref={targetRef(settingsTabTargetId(tab), { kind: "tab", label, route: "settings" })}
                    type="button"
                    className={`settings-view-sidebar-tab${activeTab === tab ? " is-active" : ""}${showUpdateDot ? " has-store-update" : ""}${showJobBadge ? " has-store-jobs" : ""}`}
                    onClick={() => void requestTab(tab)}
                    title={
                      sidebarIconsOnly
                        ? showJobBadge
                          ? `${label} · ${storeJobCount} updating`
                          : label
                        : undefined
                    }
                  >
                    {resolvePluginHeaderIcon(tabEmoji)}
                    <span>{label}</span>
                    {showJobBadge || showUpdateDot ? (
                      <span className="store-tab-badges" aria-hidden={false}>
                        {showJobBadge ? (
                          <span
                            className="store-job-badge"
                            aria-label={`${storeJobCount} store job${storeJobCount === 1 ? "" : "s"} running`}
                          >
                            {storeJobBadge}
                          </span>
                        ) : null}
                        {showUpdateDot ? (
                          <span
                            className="store-update-dot store-update-dot--inline"
                            aria-label="Updates available"
                          />
                        ) : null}
                      </span>
                    ) : null}
                  </button>
                );
              })}
              {pluginTabs.length > 0 ? (
                <div className={`settings-view-sidebar-plugins${pluginsNavOpen ? " is-open" : ""}`}>
                  <button
                    type="button"
                    className={`settings-view-sidebar-tab settings-view-sidebar-plugins-toggle${activeIsPluginTab || storeIsPluginsPage ? " is-active-group" : ""}${storeIsPluginsPage && !storePluginFocus ? " is-active" : ""}`}
                    onClick={onPluginsHeaderClick}
                    aria-expanded={pluginsNavOpen}
                    title={
                      sidebarIconsOnly
                        ? `Plugins (${installedPluginCountLabel})`
                        : undefined
                    }
                  >
                    {resolvePluginHeaderIcon("🧩")}
                    <span>Installed</span>
                    <span
                      className="settings-view-sidebar-plugins-count"
                      aria-label={`${installedPluginCount} plugins installed`}
                    >
                      {installedPluginCountLabel}
                    </span>
                    <span
                      className="settings-view-sidebar-plugins-chevron"
                      aria-hidden
                      onClick={onPluginsChevronClick}
                    >
                      {pluginsNavOpen ? <Icons.ChevronDown /> : <Icons.ChevronRight />}
                    </span>
                  </button>
                  {pluginsNavOpen ? (
                    <div className="settings-view-sidebar-plugins-list" role="group" aria-label="Installed plugins">
                      {pluginTabs.map((tab) => {
                        const label = tab.label || tab.id;
                        const pluginId = (tab.plugin_id || "").trim().toLowerCase();
                        const isActive =
                          activeTab === tab.id ||
                          (storeIsPluginsPage && Boolean(storePluginFocus) && storePluginFocus === pluginId);
                        return (
                          <button
                            key={`${tab.plugin_id}:${tab.id}`}
                            ref={targetRef(settingsTabTargetId(label), {
                              kind: "tab",
                              label,
                              route: "settings",
                            })}
                            type="button"
                            className={`settings-view-sidebar-tab settings-view-sidebar-tab--plugin${isActive ? " is-active" : ""}`}
                            onClick={() => openPluginInStore(pluginId || tab.id)}
                            title={sidebarIconsOnly ? label : undefined}
                          >
                            {resolvePluginHeaderIcon(tab.icon)}
                            <span>{label}</span>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </nav>
            <div className="settings-view-sidebar-footer">
              <span className="settings-view-sidebar-version" title={`UEFN Ducky v${resolvedVersion}`}>
                UEFN Ducky v{resolvedVersion}
              </span>
            </div>
          </CtrlWheelZoomRoot>
          <SplitResizeHandle
            className="settings-view-sidebar-resize-handle"
            onDrag={onSidebarResize}
            onDragEnd={persistSidebarWidth}
            ariaLabel="Resize settings sidebar"
          />
        </div>

        <CtrlWheelZoomRoot className="settings-view-main" storageKey="uefn-panel-settings-zoom">
          {showAppearanceProfileHeader ? <AppearanceProfileHeader /> : null}

          {showHeaderSubTabs ? (
            activeTab === "Plans" ? (
              <nav className="settings-view-header-tabs no-drag" aria-label="Plans sections">
                <div className="settings-view-header-tabs-scroll">
                  {PLANS_SECTION_TABS.map((tab) => (
                    <button
                      key={tab.id}
                      ref={targetRef(`settings.plans.section.${tab.id}`, {
                        kind: "tab",
                        label: tab.label,
                        route: "settings.plans",
                      })}
                      type="button"
                      className={`settings-view-header-tab${plansSection === tab.id ? " is-active" : ""}`}
                      onClick={() => setPlansSection(tab.id)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </nav>
            ) : activeTab === "LLMs" ? (
              <div className="settings-view-header-tabs-stack no-drag">
                <nav className="settings-view-header-tabs" aria-label="LLMs sections">
                  <div className="settings-view-header-tabs-scroll">
                    {LLMS_SECTION_TABS.map((tab) => (
                      <button
                        key={tab.id}
                        ref={targetRef(`settings.llms.section.${tab.id}`, {
                          kind: "tab",
                          label: tab.label,
                          route:
                            tab.id === "skills"
                              ? "settings.skills"
                              : tab.id === "mcps"
                                ? "settings.mcp"
                                : tab.id === "memory"
                                  ? "settings.memory"
                                  : "settings.llms",
                        })}
                        type="button"
                        className={`settings-view-header-tab${llmsSection === tab.id ? " is-active" : ""}`}
                        onClick={() => setLlmsSection(tab.id)}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>
                </nav>
                {llmsSection === "memory" ? (
                  <nav className="settings-view-header-tabs" aria-label="Memory sections">
                    <div className="settings-view-header-tabs-scroll">
                      {MEMORY_SECTION_TABS.map((tab) => (
                        <button
                          key={tab.id}
                          ref={targetRef(`settings.memory.section.${tab.id}`, {
                            kind: "tab",
                            label: tab.label,
                            route: "settings.memory",
                          })}
                          type="button"
                          className={`settings-view-header-tab settings-view-header-tab--sub${memorySection === tab.id ? " is-active" : ""}`}
                          onClick={() => setMemorySection(tab.id)}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </nav>
                ) : null}
              </div>
            ) : activeTab === "General" ? (
              <div className="settings-view-header-tabs-stack no-drag">
                <nav className="settings-view-header-tabs" aria-label="General sections">
                  <div className="settings-view-header-tabs-scroll">
                    {GENERAL_SECTION_TABS.map((tab) => (
                      <button
                        key={tab.id}
                        ref={targetRef(`settings.general.section.${tab.id}`, {
                          kind: "tab",
                          label: tab.label,
                          route: tab.id === "log_errors" ? "settings.log_errors" : "settings.general",
                        })}
                        type="button"
                        className={`settings-view-header-tab${generalSection === tab.id ? " is-active" : ""}`}
                        onClick={() => setGeneralSection(tab.id)}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>
                </nav>
                {generalSection === "log_errors" ? (
                  <nav className="settings-view-header-tabs" aria-label="Log & Errors sections">
                    <div className="settings-view-header-tabs-scroll">
                      {LOG_ERRORS_SECTION_TABS.map((tab) => (
                        <button
                          key={tab.id}
                          ref={targetRef(`settings.log.section.${tab.id}`, {
                            kind: "tab",
                            label: tab.label,
                            route: "settings.log_errors",
                          })}
                          type="button"
                          className={`settings-view-header-tab settings-view-header-tab--sub${logErrorsSection === tab.id ? " is-active" : ""}`}
                          onClick={() => setLogErrorsSection(tab.id)}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </nav>
                ) : null}
              </div>
            ) : activeTab === "Discord" ? (
              <nav className="settings-view-header-tabs no-drag" aria-label="Discord sections">
                <div className="settings-view-header-tabs-scroll">
                  {DISCORD_SECTION_TABS.map((tab) => (
                    <button
                      key={tab.id}
                      ref={targetRef(`settings.discord.section.${tab.id}`, {
                        kind: "tab",
                        label: tab.label,
                        route: "settings.discord",
                      })}
                      type="button"
                      className={`settings-view-header-tab${discordSection === tab.id ? " is-active" : ""}`}
                      onClick={() => setDiscordSection(tab.id)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </nav>
            ) : (
              <nav className="settings-view-header-tabs no-drag" aria-label="Section">
                <div className="settings-view-header-tabs-scroll">
                  {activeTab === "Audio"
                    ? AUDIO_SECTION_TABS.map((tab) => (
                        <button
                          key={tab.id}
                          ref={targetRef(`settings.audio.section.${tab.id}`, {
                            kind: "tab",
                            label: tab.label,
                            route: "settings.audio",
                          })}
                          type="button"
                          className={`settings-view-header-tab${audioSection === tab.id ? " is-active" : ""}`}
                          onClick={() => setAudioSection(tab.id)}
                        >
                          {tab.label}
                        </button>
                      ))
                    : null}
                </div>
              </nav>
            )
          ) : null}

          <section ref={contentTargetRef} className="settings-view-content selectable-text no-drag">
            {activeTab === "Support" && <SupportTab />}
            {storeMounted ? (
              <div
                className="store-tab-host"
                hidden={activeTab !== "Store"}
                aria-hidden={activeTab !== "Store"}
              >
                <StoreTab />
              </div>
            ) : null}
            {activeTab === "General" && generalSection === "general" && <AddToUefnTab />}
            {activeTab === "General" && generalSection === "log_errors" && (
              <LogErrorsTab sectionTab={logErrorsSection} />
            )}
            {activeTab === "Duckies" && <DuckiesTab />}
            {activeTab === "Plans" && <PlansTab sectionTab={plansSection} />}
            {activeTab === "LLMs" && llmsSection === "llms" && <AgentTab />}
            {activeTab === "LLMs" && (llmsSection === "skills" || llmsSection === "mcps") && (
              <SkillsMcpTab sectionTab={llmsSection} />
            )}
            {activeTab === "LLMs" && llmsSection === "memory" && (
              <MemoryTab sectionTab={memorySection} />
            )}
            {SOURCE_CONTROL_TAB_ENABLED && activeTab === "Source Control" && <SourceControlTab />}
            {activeTab === "Appearance" && <AppearanceTab />}
            {activeTab === "Audio" && <AudioTab sectionTab={audioSection} />}
            {activePluginTab?.id === "Discord" ? (
              <DiscordTab sectionTab={discordSection} />
            ) : activePluginTab ? (
              renderPluginTabBody(activePluginTab)
            ) : null}
          </section>
        </CtrlWheelZoomRoot>
      </div>
    </div>
  );
}

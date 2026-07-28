import type { SettingsTab } from "../views/SettingsView";
import { emitAppHook } from "../sfx/appHooks";

/** Fired after DuckyOS login/logout so Settings can show/hide login-gated tabs (Store). */
export const DUCKYOS_ACCOUNT_CHANGED = "duckyos-account-changed";

const LAST_TAB_KEY = "uefn-panel-settings-active-tab";
const LAST_SECTION_KEY = "uefn-panel-settings-sections";

/** Last Settings sidebar tab — survives unmount when switching editor tabs. */
export function readLastSettingsTab(): SettingsTab | null {
  try {
    const raw = (localStorage.getItem(LAST_TAB_KEY) || "").trim();
    if (!raw) return null;
    const tab = normalizeSettingsTab(raw);
    // Migrated from former LLMs → Plans header nesting.
    if (tab === "LLMs" && readLastSettingsSections().llms === "plans") {
      rememberSettingsSections({ llms: "llms" });
      return "Plans";
    }
    return tab;
  } catch {
    return null;
  }
}

export function rememberSettingsTab(tab: SettingsTab): void {
  try {
    localStorage.setItem(LAST_TAB_KEY, normalizeSettingsTab(tab));
  } catch {
    /* ignore quota / private mode */
  }
}

type SettingsSections = {
  /** LLMs header section: llms | skills | mcps | memory */
  llms?: string;
  /** @deprecated migrated into `llms` */
  skillsMcp?: string;
  /** General header section: general | log_errors */
  general?: string;
  /** Log | Errors leaf under General → Log & Errors */
  logErrors?: string;
  /** Templates | Project Plans under Settings → Plans */
  plans?: string;
  /** Entries | Context leaf under LLMs → Memory */
  memory?: string;
  audio?: string;
  /** Discord header section: bots | commands */
  discord?: string;
};

/** Sidebar tabs removed when nested under another Settings tab. */
export function normalizeSettingsTab(tab: SettingsTab): SettingsTab {
  if (tab === "Skills & MCP" || tab === "Memory") return "LLMs";
  if (tab === "Log & Errors") return "General";
  return tab;
}

export function readLastSettingsSections(): SettingsSections {
  try {
    const raw = localStorage.getItem(LAST_SECTION_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as SettingsSections;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function rememberSettingsSections(patch: SettingsSections): void {
  try {
    const next = { ...readLastSettingsSections(), ...patch };
    localStorage.setItem(LAST_SECTION_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}

let pendingTab: SettingsTab | null = null;
let pendingDuckyProfileId: string | null = null;
let pendingNewDucky = false;
let duckyDeepLinkEpoch = 0;
/** No-project / welcome: full-page settings view. */
let openSettingsView: (() => void) | null = null;
/** Project open: VS Code–style singleton editor tab. Preferred when registered. */
let openSettingsEditorTab: (() => void) | null = null;
const tabConsumers = new Set<(tab: SettingsTab) => void>();
const duckyDeepLinkConsumers = new Set<(epoch: number) => void>();
let lastRequestAt = 0;

function flushPendingTab(): void {
  const tab = pendingTab;
  if (!tab || tabConsumers.size === 0) return;
  pendingTab = null;
  for (const consumer of tabConsumers) consumer(tab);
}

function flushDuckyDeepLink(): void {
  if (duckyDeepLinkConsumers.size === 0) return;
  for (const consumer of duckyDeepLinkConsumers) consumer(duckyDeepLinkEpoch);
}

/** App-level: switch to the settings view when no project / welcome. */
export function registerOpenSettingsView(fn: () => void): () => void {
  openSettingsView = fn;
  return () => {
    if (openSettingsView === fn) openSettingsView = null;
  };
}

/** ChatView: open/focus the Settings editor tab when a project is loaded. */
export function registerOpenSettingsEditorTab(fn: () => void): () => void {
  openSettingsEditorTab = fn;
  return () => {
    if (openSettingsEditorTab === fn) openSettingsEditorTab = null;
  };
}

/** SettingsView: apply a pending tab once mounted (or immediately if already open). */
export function registerSettingsTabConsumer(fn: (tab: SettingsTab) => void): () => void {
  tabConsumers.add(fn);
  flushPendingTab();
  return () => tabConsumers.delete(fn);
}

/** DuckiesTab: react when a create-flow deep-link targets a profile. */
export function registerDuckyProfileDeepLink(fn: (epoch: number) => void): () => void {
  duckyDeepLinkConsumers.add(fn);
  if (pendingDuckyProfileId) fn(duckyDeepLinkEpoch);
  return () => duckyDeepLinkConsumers.delete(fn);
}

export function requestOpenSettings(
  tab?: SettingsTab,
  opts?: { duckyProfileId?: string; newDucky?: boolean },
): void {
  const now = Date.now();
  if (now - lastRequestAt < 500) return;
  lastRequestAt = now;

  if (tab) {
    pendingTab = normalizeSettingsTab(tab);
    // Legacy sidebar tabs → nested header section (UiRpcBridge may also fire a finer section).
    if (tab === "Skills & MCP") {
      const section = readLastSettingsSections().llms === "mcps" || readLastSettingsSections().skillsMcp === "mcps"
        ? "mcps"
        : "skills";
      queueMicrotask(() =>
        window.dispatchEvent(
          new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section } }),
        ),
      );
    } else if (tab === "Log & Errors") {
      const section = readLastSettingsSections().logErrors === "errors" ? "errors" : "log";
      queueMicrotask(() =>
        window.dispatchEvent(
          new CustomEvent("ducky:settings-section", { detail: { tab: "General", section } }),
        ),
      );
    } else if (tab === "Memory") {
      const section = readLastSettingsSections().memory === "context" ? "context" : "entries";
      queueMicrotask(() =>
        window.dispatchEvent(
          new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section } }),
        ),
      );
    }
  }
  if (opts?.duckyProfileId) {
    pendingDuckyProfileId = opts.duckyProfileId;
    duckyDeepLinkEpoch += 1;
  }
  if (opts?.newDucky) {
    pendingNewDucky = true;
    duckyDeepLinkEpoch += 1;
  }
  // Prefer editor tab when ChatView is mounted (project open).
  if (openSettingsEditorTab) {
    openSettingsEditorTab();
  } else {
    openSettingsView?.();
  }
  flushPendingTab();
  if (opts?.duckyProfileId || opts?.newDucky) flushDuckyDeepLink();
  emitAppHook("settings.opened", tab ? { tab: normalizeSettingsTab(tab) } : undefined);
}

/** DuckiesTab: consume a deep-link profile id once (clears after read). */
export function takePendingDuckyProfileId(): string | null {
  const id = pendingDuckyProfileId;
  pendingDuckyProfileId = null;
  return id;
}

/** DuckiesTab: open New Ducky modal once (clears after read). */
export function takePendingNewDucky(): boolean {
  const open = pendingNewDucky;
  pendingNewDucky = false;
  return open;
}

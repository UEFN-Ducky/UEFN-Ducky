/** Nested drill inside a Settings sidebar tab (Store layers, Duckies detail, …). */
export type SettingsDrill =
  | { type: "store"; section: string | null; slug: string | null }
  | { type: "duckies"; profileId: string | null }
  | { type: "llms"; providerId: string | null; usage: boolean }
  | { type: "plans"; planKey: string | null }
  | { type: "memory"; entryName: string | null }
  | { type: "skills"; packId: string | null; fileId?: string | null }
  | { type: "mcps"; pluginId: string | null; toolName?: string | null }
  | { type: "discord"; botId: string | null };

export type SettingsNavLocation = {
  kind: "settings";
  tab: string;
  /** Header sub-tab (Skills/MCPs, Log/Errors, …). */
  sectionTab?: string;
  drill?: SettingsDrill;
  name: string;
};

export function sameSettingsDrill(a?: SettingsDrill, b?: SettingsDrill): boolean {
  if (a === b) return true;
  if (!a || !b) return !a && !b;
  if (a.type !== b.type) return false;
  if (a.type === "store" && b.type === "store") {
    return a.section === b.section && a.slug === b.slug;
  }
  if (a.type === "duckies" && b.type === "duckies") {
    return a.profileId === b.profileId;
  }
  if (a.type === "llms" && b.type === "llms") {
    return a.providerId === b.providerId && a.usage === b.usage;
  }
  if (a.type === "plans" && b.type === "plans") {
    return a.planKey === b.planKey;
  }
  if (a.type === "memory" && b.type === "memory") {
    return a.entryName === b.entryName;
  }
  if (a.type === "skills" && b.type === "skills") {
    return a.packId === b.packId && (a.fileId || null) === (b.fileId || null);
  }
  if (a.type === "mcps" && b.type === "mcps") {
    return a.pluginId === b.pluginId && (a.toolName || null) === (b.toolName || null);
  }
  if (a.type === "discord" && b.type === "discord") {
    return a.botId === b.botId;
  }
  return false;
}

export function sameSettingsLocation(a: SettingsNavLocation, b: SettingsNavLocation): boolean {
  return (
    a.tab === b.tab &&
    (a.sectionTab || "") === (b.sectionTab || "") &&
    sameSettingsDrill(a.drill, b.drill)
  );
}

type SettingsApplier = (loc: SettingsNavLocation) => void;

let settingsApplier: SettingsApplier | null = null;
let pendingSettingsLoc: SettingsNavLocation | null = null;

/** SettingsView registers while mounted; pending loc flushes on register. */
export function registerSettingsHistoryApplier(fn: SettingsApplier): () => void {
  settingsApplier = fn;
  if (pendingSettingsLoc) {
    const loc = pendingSettingsLoc;
    pendingSettingsLoc = null;
    fn(loc);
  }
  return () => {
    if (settingsApplier === fn) settingsApplier = null;
  };
}

/** NavigationHistory calls this when applying a settings location. */
export function applySettingsHistory(loc: SettingsNavLocation): void {
  if (settingsApplier) {
    settingsApplier(loc);
  } else {
    pendingSettingsLoc = loc;
  }
}

type DrillListener = (loc: SettingsNavLocation) => void;
const drillListeners = new Set<DrillListener>();
/** Last published apply — kept until the matching tab consumes it (tab may mount later). */
let pendingDrillLoc: SettingsNavLocation | null = null;

/** SettingsView publishes after switching sidebar tab so child slides can restore. */
export function publishSettingsDrillApply(loc: SettingsNavLocation): void {
  pendingDrillLoc = loc;
  for (const fn of drillListeners) fn(loc);
}

/** Store/Duckies/LLMs/Plans subscribe to restore their slide state. */
export function subscribeSettingsDrillApply(fn: DrillListener): () => void {
  drillListeners.add(fn);
  if (pendingDrillLoc) fn(pendingDrillLoc);
  return () => {
    drillListeners.delete(fn);
  };
}

/** Call after the owning tab applied a matching location. */
export function clearPendingSettingsDrill(tab: string): void {
  if (pendingDrillLoc?.tab === tab) pendingDrillLoc = null;
}

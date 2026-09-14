export const HEADER_VISIBILITY_KEY = "uefn-header-visibility";
export const HEADER_VISIBILITY_EVENT = "uefn-header-visibility";

export type HeaderButtonGroup = "builtin" | "plugin";

export type HeaderCatalogEntry = {
  id: string;
  label: string;
  group: HeaderButtonGroup;
};

export const BUILTIN_HEADER_BUTTONS: HeaderCatalogEntry[] = [
  { id: "nav", label: "Back and forward", group: "builtin" },
  { id: "leftSidebar", label: "Left sidebar", group: "builtin" },
  { id: "rightSidebar", label: "Right sidebar", group: "builtin" },
  { id: "search", label: "Search", group: "builtin" },
];

export type HeaderVisibilitySnapshot = {
  hidden: string[];
};

export function pluginHeaderButtonId(pluginId: string, buttonId: string): string {
  return `plugin:${(pluginId || buttonId).trim().toLowerCase()}:${buttonId}`;
}

export function discordHeaderButtonId(): string {
  return pluginHeaderButtonId("discord", "discord");
}

function uniqueIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw) {
    const id = String(item || "").trim();
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

function readDiscordShowInHeader(): boolean {
  try {
    const raw = localStorage.getItem("uefn-plugin-ui-prefs");
    const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
    const discord = all.discord && typeof all.discord === "object" ? all.discord : {};
    return discord.showInHeader === true;
  } catch {
    return false;
  }
}

function defaultSnapshot(): HeaderVisibilitySnapshot {
  return { hidden: readDiscordShowInHeader() ? [] : [discordHeaderButtonId()] };
}

export function normalizeHeaderVisibility(raw: unknown): HeaderVisibilitySnapshot {
  if (!raw || typeof raw !== "object") return defaultSnapshot();
  const data = raw as { hidden?: unknown };
  if (!("hidden" in data)) return defaultSnapshot();
  return { hidden: uniqueIds(data.hidden) };
}

export function persistHeaderVisibility(snapshot: HeaderVisibilitySnapshot): void {
  writeHeaderVisibility(snapshot);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(HEADER_VISIBILITY_EVENT));
  }
}

function writeHeaderVisibility(snapshot: HeaderVisibilitySnapshot): void {
  try {
    localStorage.setItem(HEADER_VISIBILITY_KEY, JSON.stringify(snapshot));
  } catch {
    /* ignore quota */
  }
}

export function readHeaderVisibility(): HeaderVisibilitySnapshot {
  try {
    const raw = localStorage.getItem(HEADER_VISIBILITY_KEY);
    if (raw) return normalizeHeaderVisibility(JSON.parse(raw));
  } catch {
    /* ignore */
  }
  const snapshot = defaultSnapshot();
  writeHeaderVisibility(snapshot);
  return snapshot;
}

export function isHeaderButtonVisible(snapshot: HeaderVisibilitySnapshot, id: string): boolean {
  return !snapshot.hidden.includes(id);
}

export function withHeaderButtonVisible(
  snapshot: HeaderVisibilitySnapshot,
  id: string,
  visible: boolean,
): HeaderVisibilitySnapshot {
  const hidden = snapshot.hidden.filter((item) => item !== id);
  if (!visible) hidden.push(id);
  return { hidden };
}

export function headerButtonCatalog(
  pluginButtons: Array<{ id: string; title?: string; plugin_id?: string }>,
): HeaderCatalogEntry[] {
  const plugins = pluginButtons.map((btn) => ({
    id: pluginHeaderButtonId(btn.plugin_id || btn.id, btn.id),
    label: (btn.title || btn.id).trim() || btn.id,
    group: "plugin" as const,
  }));
  return [...BUILTIN_HEADER_BUTTONS, ...plugins];
}

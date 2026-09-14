import type { EditorTab } from "../../types/panel";
import { recentTabs } from "./quickOpenUtils";

export const QUERY_KEY = "uefn-panel-quick-open-history";
export const SERVICE_KEY = "uefn-panel-quick-open-services";
export const RESOURCE_KEY = "uefn-panel-quick-open-resources";
export const RECENTS_CAP = 12;
export const IDLE_SERVICE_CAP = 8;

export type RecentsStore = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export type QuickOpenResourceKind = "chat" | "file" | "project" | "settings";

export type QuickOpenResource = {
  id: string;
  kind: QuickOpenResourceKind;
  label: string;
  chatId?: string;
  path?: string;
  settingsTab?: string;
};

export type IdleService = {
  id: string;
  label: string;
  kind: "chat" | "destination";
  chatId?: string;
};

const memory = new Map<string, string>();
const MEMORY_STORE: RecentsStore = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => {
    memory.set(key, value);
  },
  removeItem: (key) => {
    memory.delete(key);
  },
};

function liveStore(): RecentsStore {
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch {
    /* private mode */
  }
  return MEMORY_STORE;
}

function readJson<T>(key: string, store: RecentsStore): T | null {
  try {
    const raw = store.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown, store: RecentsStore): void {
  try {
    store.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / private mode */
  }
}

function prependUnique(list: string[], item: string, cap: number): string[] {
  const next = [item, ...list.filter((x) => x !== item)];
  return next.slice(0, cap);
}

export const DEFAULT_DESTINATIONS: IdleService[] = [
  { id: "settings:Store", label: "Plugins", kind: "destination" },
  { id: "settings:Duckies", label: "Duckies", kind: "destination" },
  { id: "settings:Plans", label: "Plans", kind: "destination" },
  { id: "settings:LLMs", label: "LLMs", kind: "destination" },
  { id: "settings:Appearance", label: "Appearance", kind: "destination" },
  { id: "files", label: "Files", kind: "destination" },
  { id: "settings:General", label: "Settings", kind: "destination" },
  { id: "settings:Audio", label: "Audio", kind: "destination" },
];

export function resourceTypeLabel(kind: QuickOpenResourceKind): string {
  if (kind === "chat") return "Ducky";
  if (kind === "file") return "File";
  if (kind === "project") return "Project";
  return "Settings";
}

export function readQueries(store: RecentsStore = liveStore()): string[] {
  const raw = readJson<unknown>(QUERY_KEY, store);
  if (!Array.isArray(raw)) return [];
  return raw.filter((q): q is string => typeof q === "string" && q.trim().length > 0).slice(0, RECENTS_CAP);
}

export function pushQuery(query: string, store: RecentsStore = liveStore()): string[] {
  const trimmed = query.trim();
  if (!trimmed) return readQueries(store);
  const next = prependUnique(readQueries(store), trimmed, RECENTS_CAP);
  writeJson(QUERY_KEY, next, store);
  return next;
}

export function clearQueries(store: RecentsStore = liveStore()): void {
  try {
    store.removeItem(QUERY_KEY);
  } catch {
    /* ignore */
  }
}

export function readServices(store: RecentsStore = liveStore()): string[] {
  const raw = readJson<unknown>(SERVICE_KEY, store);
  if (!Array.isArray(raw)) return [];
  return raw.filter((id): id is string => typeof id === "string" && id.length > 0).slice(0, RECENTS_CAP);
}

export function pushService(id: string, store: RecentsStore = liveStore()): string[] {
  const trimmed = id.trim();
  if (!trimmed) return readServices(store);
  const next = prependUnique(readServices(store), trimmed, RECENTS_CAP);
  writeJson(SERVICE_KEY, next, store);
  return next;
}

function isResource(value: unknown): value is QuickOpenResource {
  if (!value || typeof value !== "object") return false;
  const r = value as QuickOpenResource;
  return typeof r.id === "string" && typeof r.label === "string" && typeof r.kind === "string";
}

export function readResources(store: RecentsStore = liveStore()): QuickOpenResource[] {
  const raw = readJson<unknown>(RESOURCE_KEY, store);
  if (!Array.isArray(raw)) return [];
  return raw.filter(isResource).slice(0, RECENTS_CAP);
}

export function pushResource(resource: QuickOpenResource, store: RecentsStore = liveStore()): QuickOpenResource[] {
  if (!resource.id || !resource.label) return readResources(store);
  const next = [resource, ...readResources(store).filter((r) => r.id !== resource.id)].slice(0, RECENTS_CAP);
  writeJson(RESOURCE_KEY, next, store);
  return next;
}

export function mergeIdleServices(
  recentIds: string[],
  chats: { id: string; name: string }[],
  openTabs: Pick<EditorTab, "kind" | "chatId" | "name">[],
  limit = IDLE_SERVICE_CAP,
): IdleService[] {
  const chatName = new Map(chats.map((c) => [c.id, c.name]));
  const out: IdleService[] = [];
  const seen = new Set<string>();
  const push = (service: IdleService) => {
    if (seen.has(service.id) || out.length >= limit) return;
    seen.add(service.id);
    out.push(service);
  };

  for (const id of recentIds) {
    if (id.startsWith("chat:")) {
      const chatId = id.slice(5);
      const name = chatName.get(chatId);
      if (name) push({ id, label: name, kind: "chat", chatId });
      continue;
    }
    const dest = DEFAULT_DESTINATIONS.find((d) => d.id === id);
    if (dest) push(dest);
  }

  for (const tab of [...openTabs].reverse()) {
    if (tab.kind === "chat" && tab.chatId) {
      push({ id: `chat:${tab.chatId}`, label: tab.name, kind: "chat", chatId: tab.chatId });
    }
  }

  for (const dest of DEFAULT_DESTINATIONS) push(dest);
  return out;
}

export function tabToResource(tab: EditorTab): QuickOpenResource | null {
  if (tab.kind === "file" && tab.path) {
    return { id: `file:${tab.path}`, kind: "file", label: tab.name, path: tab.path };
  }
  if (tab.kind === "chat" && tab.chatId) {
    return { id: `chat:${tab.chatId}`, kind: "chat", label: tab.name, chatId: tab.chatId };
  }
  if (tab.kind === "settings") {
    return { id: "settings:main", kind: "settings", label: tab.name || "Settings", settingsTab: "General" };
  }
  return null;
}

export function mergeIdleResources(
  stored: QuickOpenResource[],
  openTabs: EditorTab[],
  limit = RECENTS_CAP,
): QuickOpenResource[] {
  const out: QuickOpenResource[] = [];
  const seen = new Set<string>();
  const push = (resource: QuickOpenResource | null) => {
    if (!resource || seen.has(resource.id) || out.length >= limit) return;
    seen.add(resource.id);
    out.push(resource);
  };
  for (const resource of stored) push(resource);
  for (const tab of recentTabs(openTabs)) push(tabToResource(tab));
  return out;
}

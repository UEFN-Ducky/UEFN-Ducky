/**
 * Plugin chat dock: Ducky's own chat, docked inside a plugin pane.
 *
 * A plugin calls `chat.open` (bridge) and the host renders the real ChatPane at the bottom or
 * right of that pane, bound to a conversation focused on the plugin (its brief becomes the
 * ducky's personality). The plugin can collapse, expand, resize, restyle (CSS variables) or
 * close it with `chat.set` / `chat.close`, and hears `chat.state` back. `place: "float"` shows it as a
 * card over the plugin (bottom right, `offset` px up) that pops in and out, with an agent picker
 * in its header when the plugin passes `choices`.
 */
import { DEFAULT_BUNDLED_DUCKY_STYLE } from "../generated/bundledDuckies";
import { getApi } from "../hooks/usePanelApi";

export type DockPlace = "bottom" | "right" | "float";

/** A chat the floating dock's header can switch to (the plugin's own list). */
export type DockChoice = { id: string; title: string };

export type DockState = {
  pluginId: string;
  open: boolean;
  expanded: boolean;
  place: DockPlace;
  /** Height (bottom) or width (right) in px while expanded. */
  size: number;
  title: string;
  convId: string;
  /** CSS custom properties applied to the dock (already sanitized). */
  theme: Record<string, string>;
  /** Float only: px between the pane's bottom and the card (room for the plugin's own button). */
  offset: number;
  /** Float only: the chats its header picker offers. */
  choices: DockChoice[];
  /** Float only: playing its close animation; `open` turns false when it ends. */
  leaving?: boolean;
};

export type DockOpenParams = {
  key?: unknown;
  title?: unknown;
  brief?: unknown;
  conv_id?: unknown;
  place?: unknown;
  size?: unknown;
  expanded?: unknown;
  theme?: unknown;
  offset?: unknown;
  choices?: unknown;
};

const SIZES: Record<DockPlace, { min: number; max: number; def: number }> = {
  bottom: { min: 120, max: 900, def: 320 },
  right: { min: 260, max: 760, def: 380 },
  float: { min: 300, max: 760, def: 400 },
};
/** How long the floating card's close animation runs (plugin-ui.css plugin-chat-dock-out). */
export const LEAVE_MS = 160;

const states = new Map<string, DockState>();
const subs = new Set<() => void>();
const notify = () => subs.forEach((f) => f());

export function subscribeDocks(fn: () => void): () => void {
  subs.add(fn);
  return () => subs.delete(fn);
}

export function getDock(tabId: string): DockState | undefined {
  return states.get(tabId);
}

export const placeOf = (v: unknown, fallback: DockPlace = "bottom"): DockPlace =>
  v === "right" || v === "bottom" || v === "float" ? v : fallback;

export const offsetOf = (v: unknown, fallback = 16): number =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(400, Math.max(0, Math.round(v))) : fallback;

/** The plugin's chat list for the float header: ids and titles only, at most 40. */
export function sanitizeChoices(raw: unknown): DockChoice[] {
  if (!Array.isArray(raw)) return [];
  const out: DockChoice[] = [];
  for (const c of raw) {
    const id = c && typeof c === "object" ? str((c as { id?: unknown }).id, 80) : "";
    if (!id || out.some((o) => o.id === id)) continue;
    out.push({ id, title: str((c as { title?: unknown }).title, 80) || "Chat" });
    if (out.length >= 40) break;
  }
  return out;
}

export function clampSize(place: DockPlace, v: unknown): number {
  const s = SIZES[place];
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : s.def;
  return Math.min(s.max, Math.max(s.min, n));
}

/** Only `--custom-properties` with plain values: no url(), no rule breaks, so a plugin can
 * recolor the dock but never inject styles or fetch anything. */
export function sanitizeTheme(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!/^--[a-z0-9-]{1,40}$/i.test(k) || typeof v !== "string") continue;
    const val = v.trim();
    if (!val || val.length > 80 || /url\(|expression|@import|[;{}<>\\]/i.test(val)) continue;
    out[k] = val;
  }
  return out;
}

export function setDock(tabId: string, patch: Partial<DockState>): DockState | undefined {
  const cur = states.get(tabId);
  if (!cur) return undefined;
  const place = placeOf(patch.place, cur.place);
  const next: DockState = {
    ...cur,
    ...patch,
    place,
    size: patch.size !== undefined || place !== cur.place ? clampSize(place, patch.size ?? cur.size) : cur.size,
  };
  states.set(tabId, next);
  notify();
  return next;
}

export function closeDock(tabId: string): void {
  const cur = states.get(tabId);
  if (cur?.place !== "float" || !cur.open) {
    setDock(tabId, { open: false });
    return;
  }
  if (cur.leaving) return;
  setDock(tabId, { leaving: true });  // the card plays its close animation first
  setTimeout(() => {
    if (states.get(tabId)?.leaving) setDock(tabId, { open: false, leaving: false });
  }, LEAVE_MS);
}

/** The float header's picker: show another of the plugin's chats in the same card. */
export function switchDockChat(tabId: string, choice: DockChoice): void {
  dockChatIds.add(choice.id);
  setDock(tabId, { convId: choice.id, title: choice.title });
}

export function dropDock(tabId: string): void {
  if (states.delete(tabId)) notify();
}

// ---- the conversation ------------------------------------------------------------------------

/** Chats the dock made (or bound): the chat list must not open them as full tabs. */
const dockChatIds = new Set<string>();
let creating = 0;

export function isPluginDockChat(conv: { id?: string; title?: string }): boolean {
  if (conv.id && dockChatIds.has(conv.id)) return true;
  return creating > 0 && !(conv.title || "").trim();
}

export function dockPersonality(title: string, brief: string): string {
  const head = `You are the assistant built into the "${title}" plugin; the person is chatting with you from inside its panel. ` +
    "Work through this plugin's own tools first and keep replies short.";
  return (brief ? `${head}\n\n${brief}` : head).slice(0, 2000);
}

const memoKey = (pluginId: string, key: string) => `ducky-plugin-chat:${pluginId}:${key || "main"}`;

/** Lookup of the chats the window knows (set by the pane): a remembered chat that was deleted is
 * replaced instead of reopened. */
let knownChat: ((id: string) => boolean) | null = null;
export function registerChatLookup(fn: ((id: string) => boolean) | null): void {
  knownChat = fn;
}

/** The plugin's conversation for `key`: an explicit conv_id, the remembered one, or a new chat. */
export async function ensurePluginChat(pluginId: string, key: string, title: string, brief: string, convId = ""): Promise<string> {
  if (convId) {
    dockChatIds.add(convId);
    return convId;
  }
  let remembered = "";
  try {
    remembered = localStorage.getItem(memoKey(pluginId, key)) || "";
  } catch {
    /* storage off: a new chat each time */
  }
  if (remembered && (!knownChat || knownChat(remembered))) {
    dockChatIds.add(remembered);
    return remembered;
  }
  const api = getApi();
  if (!api?.create_conversation) throw new Error("chats unavailable");
  creating += 1;
  try {
    const conv = await api.create_conversation("", DEFAULT_BUNDLED_DUCKY_STYLE, undefined, {
      title,
      ducky_name: title,
      ducky_style: DEFAULT_BUNDLED_DUCKY_STYLE,
      ducky_personality: dockPersonality(title, brief),
      coding_agent: "ducky",
      disabled_tool_ids: [],
    });
    dockChatIds.add(conv.id);
    try {
      localStorage.setItem(memoKey(pluginId, key), conv.id);
    } catch {
      /* not remembered */
    }
    return conv.id;
  } finally {
    creating = Math.max(0, creating - 1);
  }
}

const str = (v: unknown, max: number) => (typeof v === "string" ? v.trim().slice(0, max) : "");

/** Bridge `chat.open`: show the dock in this pane, bound to the plugin's conversation. */
export async function openDock(tabId: string, pluginId: string, params: DockOpenParams): Promise<DockState> {
  const title = str(params.title, 60) || pluginId;
  const place = placeOf(params.place, getDock(tabId)?.place);
  const convId = await ensurePluginChat(pluginId, str(params.key, 80), title, str(params.brief, 1500), str(params.conv_id, 80));
  const prev = states.get(tabId);
  const next: DockState = {
    pluginId,
    open: true,
    // a floating card is always open full size: closing it is how it goes away
    expanded: place === "float" || (typeof params.expanded === "boolean" ? params.expanded : prev?.expanded ?? false),
    place,
    size: clampSize(place, params.size ?? prev?.size),
    title,
    convId,
    theme: params.theme === undefined ? prev?.theme ?? {} : sanitizeTheme(params.theme),
    offset: offsetOf(params.offset, prev?.offset),
    choices: params.choices === undefined ? prev?.choices ?? [] : sanitizeChoices(params.choices),
    leaving: false,
  };
  states.set(tabId, next);
  notify();
  return next;
}

/** What the plugin hears back (`chat.state` event and the calls' result). */
export function dockSnapshot(s: DockState | undefined, running = false) {
  return s
    ? { open: s.open, expanded: s.expanded, place: s.place, size: s.size, conv_id: s.convId, running }
    : { open: false, expanded: false, place: "bottom" as DockPlace, size: SIZES.bottom.def, conv_id: "", running: false };
}

/**
 * What a tool's card shows in the chat beside the call: the media files its result names
 * (played on the card), a plugin's own `chat_view` (title, blocks, which panel Open shows)
 * and, for ducky_ui_navigate, the place its Open button goes. Pure parsing; the links and
 * the owning plugin come from hooks/useToolView.
 */
import type { RichBlock } from "../../types/richContent";

export type ToolViewNavigate = { route: string; itemId: string; label: string };

export type ToolViewSpec = {
  title: string;
  /** Paths as the result spelled them (signed by the app before they are shown). */
  media: string[];
  blocks: RichBlock[];
  /** Panel the Open button shows (the plugin's first panel when empty). */
  openPanel: string;
  openLabel: string;
  navigate: ToolViewNavigate | null;
};

const MEDIA_EXT = /\.(png|jpe?g|webp|gif|bmp|mp4|webm|mov|mkv|mp3|wav|ogg|m4a|flac|pdf|svg)$/i;
// A full path on this PC: C:\…, C:/…, \\server\share\…, or ~/…
const FULL_PATH = /^(?:[A-Za-z]:[\\/]|\\\\[^\\]|~[\\/])/;
const MAX_MEDIA = 12;
const MAX_BLOCKS = 24;
const BLOCK_TYPES = new Set(["heading", "paragraph", "list", "code", "table", "key_value", "callout", "accordion"]);

const ROUTE_LABELS: Record<string, string> = {
  settings: "Settings",
  "settings.store": "the Store",
  "settings.llms": "AI models",
  "settings.mcp": "MCPs",
  "settings.mcp_plugins": "Plugins",
  "settings.skills": "Skills",
  "settings.appearance": "Appearance",
  "settings.audio": "Audio",
  "settings.duckies": "Duckies",
  "settings.plans": "Plans",
  "settings.memory": "Memory",
  "settings.languages": "Languages",
  "settings.log_errors": "Logs",
  "settings.app_data": "App data",
  "settings.permissions": "Permissions",
  chat: "the chat",
  chats: "Chats",
  files: "Files",
  changes: "the Ledger",
  workflows: "Workflows",
  skills_studio: "Skills studio",
  terminals: "Terminals",
  plans: "Plans",
  project_picker: "Projects",
};

/** `mcp__uefn__imk_render_get`, `uefn.imk_render_get` → `imk_render_get`. */
export function bareToolName(name: string): string {
  let text = (name || "").trim();
  for (const sep of ["__", "/", "."]) {
    const cut = text.lastIndexOf(sep);
    if (cut >= 0) text = text.slice(cut + sep.length);
  }
  return text;
}

/** The result as JSON, through the wrappers agents add (a JSON string inside a string,
 *  MCP text blocks, Claude Code's trailing "[Image: …]" lines). null when it isn't JSON. */
export function parseToolResult(raw: unknown): unknown {
  let value: unknown = raw;
  for (let i = 0; i < 4; i++) {
    if (typeof value === "string") {
      const text = value.trim().replace(/(?:\s*\[Image: [^\]\n]*\])+$/, "").trim();
      if (!/^[[{"]/.test(text)) return i === 0 ? null : value;
      try {
        value = JSON.parse(text);
      } catch {
        return null;
      }
      continue;
    }
    if (Array.isArray(value) && value.length && value.every((b) => b && typeof b === "object" && (b as { type?: unknown }).type === "text")) {
      const inner = value.map((b) => String((b as { text?: unknown }).text ?? "")).find((t) => /^\s*[[{]/.test(t));
      if (!inner) return value;
      value = inner;
      continue;
    }
    return value;
  }
  return value;
}

function isMediaPath(text: string): boolean {
  return text.length < 1024 && FULL_PATH.test(text) && MEDIA_EXT.test(text) && !/[\r\n]/.test(text);
}

/** Media file paths anywhere in a result (strings, `{path}` refs, lists), first seen first. */
export function mediaPathsIn(value: unknown, limit = MAX_MEDIA): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const walk = (node: unknown, depth: number) => {
    if (out.length >= limit || depth > 6 || node === null || node === undefined) return;
    if (typeof node === "string") {
      const text = node.trim();
      if (isMediaPath(text) && !seen.has(text.toLowerCase())) {
        seen.add(text.toLowerCase());
        out.push(text);
      }
      return;
    }
    if (Array.isArray(node)) {
      for (const item of node.slice(0, 200)) walk(item, depth + 1);
      return;
    }
    if (typeof node === "object") {
      // Pictures sent as data are drawn by the capture card; base64 is never a path.
      for (const [key, item] of Object.entries(node as Record<string, unknown>).slice(0, 200)) {
        if (/base64|^data$|_b64$/i.test(key) && typeof item === "string" && item.length > 1024) continue;
        walk(item, depth + 1);
      }
    }
  };
  walk(value, 0);
  return out;
}

function cleanBlocks(raw: unknown): RichBlock[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((b): b is RichBlock => !!b && typeof b === "object" && BLOCK_TYPES.has(String((b as { type?: unknown }).type)))
    .slice(0, MAX_BLOCKS);
}

function routeLabel(route: string, itemId: string, title: string): string {
  if (route === "plugin_panel") return title || itemId.split(":")[0] || "the plugin";
  if (route === "settings.tab") return itemId || "Settings";
  if (route === "files" && itemId) return itemId.replace(/\\/g, "/").split("/").pop() || "Files";
  return ROUTE_LABELS[route] ?? route;
}

/** The card's view, or null when it has nothing to add to the plain row. */
export function toolViewSpec(toolName: string, args: Record<string, unknown>, result: unknown): ToolViewSpec | null {
  const name = bareToolName(toolName);
  // A picture the agent looked at shows only inside its own row, never under the folded group.
  if (name === "view_image") return null;
  const parsed = parseToolResult(result);
  const obj = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  if (name === "ducky_ui_navigate") {
    const route = String(obj?.route ?? args.route ?? "").trim();
    if (!route || obj?.error || obj?.in_chat !== true) return null;
    const itemId = String(obj?.item_id ?? args.item_id ?? "").trim();
    const title = String(obj?.title ?? "").trim();
    return { title: "", media: [], blocks: [], openPanel: "", openLabel: "", navigate: { route, itemId, label: routeLabel(route, itemId, title) } };
  }
  const view = obj?.chat_view && typeof obj.chat_view === "object" && !Array.isArray(obj.chat_view)
    ? (obj.chat_view as Record<string, unknown>)
    : null;
  const listed: string[] = [];
  if (Array.isArray(view?.media)) {
    for (const row of view.media) {
      const path = typeof row === "string" ? row : row && typeof row === "object" ? String((row as { path?: unknown }).path ?? "") : "";
      if (path.trim() && isMediaPath(path.trim())) listed.push(path.trim());
    }
  }
  const found = mediaPathsIn(parsed);
  const media = [...listed, ...found.filter((p) => !listed.some((l) => l.toLowerCase() === p.toLowerCase()))].slice(0, MAX_MEDIA);
  const open = view?.open && typeof view.open === "object" ? (view.open as Record<string, unknown>) : null;
  const spec: ToolViewSpec = {
    title: String(view?.title ?? "").trim().slice(0, 160),
    media,
    blocks: cleanBlocks(view?.blocks),
    openPanel: String(open?.panel ?? "").trim(),
    openLabel: String(open?.label ?? "").trim().slice(0, 60),
    navigate: null,
  };
  return spec.title || spec.media.length || spec.blocks.length || spec.openPanel ? spec : null;
}

export function hasViewContent(spec: ToolViewSpec | null): boolean {
  return !!spec && (spec.media.length > 0 || spec.blocks.length > 0 || !!spec.title);
}

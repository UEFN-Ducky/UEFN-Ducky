import type { ReactNode } from "react";
import { requestOpenDiscordTab } from "../navigation/openDiscordTab";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { PANEL_ACTION_PREFIX, requestOpenPluginUiTab } from "../plugin-ui";
import type { PluginHeaderButton } from "./usePluginContributions";

const SETTINGS_ACTION_PREFIX = "settings:";

/** Phase-1 builtin actions for `contributes.header.buttons[].action`. */
const BUILTIN_ACTIONS: Record<string, () => void> = {
  "builtin:open-discord": () => requestOpenDiscordTab(),
};

/** Named builtin keys → emoji (no line-icon SVGs in Settings / header). */
const NAMED_EMOJI: Record<string, string> = {
  chat: "💬",
  duck: "🦆",
  globe: "🌐",
  user: "👤",
  speaker: "🔊",
};

export type PluginIconContent =
  | { kind: "image"; src: string }
  | { kind: "emoji"; emoji: string };

/** Pure resolver — images when present, else emoji (named key or raw glyph). */
export function resolvePluginIconContent(icon: string | undefined): PluginIconContent {
  const raw = (icon || "chat").trim();
  if (
    raw.startsWith("data:image/") ||
    /^https?:\/\//i.test(raw) ||
    /\.(png|jpe?g|svg|webp|gif)(\?|$)/i.test(raw)
  ) {
    return { kind: "image", src: raw };
  }
  const named = NAMED_EMOJI[raw.toLowerCase()];
  if (named) return { kind: "emoji", emoji: named };
  // Raw emoji / short label from plugin.json (e.g. "⚡", "🎨")
  return { kind: "emoji", emoji: raw || NAMED_EMOJI.chat };
}

export function resolvePluginHeaderAction(
  action: string,
  pluginId?: string,
): (() => void) | null {
  const builtin = BUILTIN_ACTIONS[action];
  if (builtin) return builtin;
  if (action.startsWith(SETTINGS_ACTION_PREFIX)) {
    const tabId = action.slice(SETTINGS_ACTION_PREFIX.length).trim();
    if (!tabId) return null;
    return () => requestOpenSettings(tabId);
  }
  // Phase 2: `panel:<panelId>` opens that plugin's ui.panels tab.
  if (action.startsWith(PANEL_ACTION_PREFIX) && pluginId) {
    const panelId = action.slice(PANEL_ACTION_PREFIX.length).trim().toLowerCase();
    if (!panelId) return null;
    const pid = pluginId.trim().toLowerCase();
    return () => requestOpenPluginUiTab(pid, panelId);
  }
  return null;
}

export function resolvePluginHeaderIcon(icon: string | undefined): ReactNode {
  const resolved = resolvePluginIconContent(icon);
  if (resolved.kind === "image") {
    return <img src={resolved.src} alt="" className="plugin-contrib-icon" draggable={false} />;
  }
  return (
    <span className="plugin-contrib-emoji" aria-hidden>
      {resolved.emoji}
    </span>
  );
}

export function sortPluginHeaderButtons(buttons: PluginHeaderButton[]): PluginHeaderButton[] {
  return [...buttons].sort((a, b) => {
    const ao = typeof a.order === "number" ? a.order : 100;
    const bo = typeof b.order === "number" ? b.order : 100;
    if (ao !== bo) return ao - bo;
    return String(a.id).localeCompare(String(b.id));
  });
}

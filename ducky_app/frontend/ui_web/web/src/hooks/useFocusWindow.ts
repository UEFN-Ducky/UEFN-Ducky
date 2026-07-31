import { useCallback } from "react";
import { getApi } from "../hooks/usePanelApi";
import {
  chatTabId,
  fileTabId,
  terminalTabId,
  settingsTabId,
  discordTabId,
  duckyProfileTabId,
  usageTabId,
  planTabId,
  type EditorTab,
} from "../types/panel";
import { parsePluginUiTabId, pluginUiTabId } from "../plugin-ui/types";
import { verseTranslatedTabId } from "../navigation/openVerseTranslatedTab";
import { basename, isVerseFile } from "../verse-editor/utils/isVerseFile";

export function useFocusWindow() {
  const openFocus = useCallback(async (focusId: string, title: string, options?: { solo?: boolean }) => {
    const api = getApi();
    if (!api) return;
    // solo=True → new OS window group; solo=False → add to primary focus group
    await api.open_focus_window(focusId, title, options?.solo ?? false);
  }, []);

  const openChatFocus = useCallback(
    async (chatId: string, name: string) => {
      await openFocus(chatTabId(chatId), name);
    },
    [openFocus],
  );

  const openFileFocus = useCallback(
    async (path: string, name: string) => {
      const norm = path.replace(/\\/g, "/");
      await openFocus(fileTabId(norm), name);
    },
    [openFocus],
  );

  /** False when the host declined — the drop landed back inside this window. */
  const openFocusAtPoint = useCallback(
    async (focusId: string, title: string, screenX: number, screenY: number) => {
      const api = getApi();
      if (!api) return false;
      return (await api.open_focus_window_at_point(focusId, title, screenX, screenY)) !== false;
    },
    [],
  );

  const raiseFocus = useCallback(async (focusId: string) => {
    const api = getApi();
    if (!api) return;
    await api.raise_focus_window(focusId);
  }, []);

  return { openFocus, openFocusAtPoint, openChatFocus, openFileFocus, raiseFocus };
}

/** WebView2/pywebview sometimes leave a once-encoded focus id (`settings%3Amain`). */
export function decodeFocusParam(raw: string): string {
  let s = (raw || "").trim();
  for (let i = 0; i < 2; i++) {
    if (!s.includes("%")) break;
    try {
      const next = decodeURIComponent(s);
      if (next === s) break;
      s = next;
    } catch {
      break;
    }
  }
  return s;
}

export type ParsedFocusId =
  | { kind: "chat"; chatId: string }
  | { kind: "file"; path: string }
  | { kind: "terminal"; sessionId: string }
  | { kind: "settings" }
  | { kind: "usage"; providerId: string }
  | { kind: "plan"; chatId: string }
  | { kind: "plugin"; tabId: string; pluginId: string; panelId: string }
  | { kind: "verse-translated"; path: string; lang: string }
  | { kind: "ducky-profile"; profileId: string };

export function parseFocusId(focusId: string): ParsedFocusId | null {
  const id = decodeFocusParam(focusId);
  if (!id) return null;

  if (id.startsWith("chat:")) {
    const chatId = id.slice(5);
    return chatId ? { kind: "chat", chatId } : null;
  }
  if (id.startsWith("file:")) {
    const path = id.slice(5);
    return path ? { kind: "file", path } : null;
  }
  if (id.startsWith("terminal:")) {
    const sessionId = id.slice(9);
    return sessionId ? { kind: "terminal", sessionId } : null;
  }
  if (id === settingsTabId() || id.startsWith("settings:")) {
    return { kind: "settings" };
  }
  if (id === discordTabId() || id.startsWith("discord:")) {
    // Legacy host Discord tabs → plugin chat panel.
    return {
      kind: "plugin",
      tabId: pluginUiTabId("discord", "discord-chat"),
      pluginId: "discord",
      panelId: "discord-chat",
    };
  }
  if (id.startsWith("ducky-profile:")) {
    const profileId = id.slice("ducky-profile:".length);
    return profileId ? { kind: "ducky-profile", profileId } : null;
  }
  if (id.startsWith("usage:")) {
    const providerId = id.slice(6);
    return providerId ? { kind: "usage", providerId } : null;
  }
  if (id.startsWith("plan:")) {
    const chatId = id.slice(5);
    return chatId ? { kind: "plan", chatId } : null;
  }
  if (id.startsWith("plugin:")) {
    const parsed = parsePluginUiTabId(id);
    if (!parsed) return null;
    return { kind: "plugin", tabId: id, pluginId: parsed.pluginId, panelId: parsed.panelId };
  }
  if (id.startsWith("verse-translated:")) {
    // verse-translated:<lang>:<path>
    const rest = id.slice("verse-translated:".length);
    const colon = rest.indexOf(":");
    if (colon <= 0) return null;
    const lang = rest.slice(0, colon);
    const path = rest.slice(colon + 1);
    return lang && path ? { kind: "verse-translated", lang, path } : null;
  }
  return null;
}

/** True when Activate must re-open the tab (Python lists it; React already dropped it). */
export function focusActivateNeedsOpen(openTabIds: readonly string[], focusId: string): boolean {
  const id = decodeFocusParam(focusId);
  return Boolean(id) && !openTabIds.includes(id);
}

export function focusIdToEditorTab(focusId: string, title: string): EditorTab | null {
  const parsed = parseFocusId(focusId);
  if (!parsed) return null;
  if (parsed.kind === "chat") {
    return { id: chatTabId(parsed.chatId), kind: "chat", name: title || "Chat", chatId: parsed.chatId };
  }
  if (parsed.kind === "terminal") {
    return {
      id: terminalTabId(parsed.sessionId),
      kind: "terminal",
      name: title || "terminal",
      terminalSessionId: parsed.sessionId,
      terminalShell: "bash",
    };
  }
  if (parsed.kind === "settings") {
    return { id: settingsTabId(), kind: "settings", name: title || "Settings" };
  }
  if (parsed.kind === "ducky-profile") {
    return {
      id: duckyProfileTabId(parsed.profileId),
      kind: "ducky-profile",
      name: title || parsed.profileId,
      path: parsed.profileId,
    };
  }
  if (parsed.kind === "usage") {
    return {
      id: usageTabId(parsed.providerId),
      kind: "usage",
      name: title || `${parsed.providerId} usage`,
      path: parsed.providerId,
    };
  }
  if (parsed.kind === "plan") {
    return {
      id: planTabId(parsed.chatId),
      kind: "plan",
      name: title || "Plan",
      chatId: parsed.chatId,
    };
  }
  if (parsed.kind === "plugin") {
    return {
      id: parsed.tabId,
      kind: "plugin",
      name: title || parsed.panelId || "Plugin",
      path: `${parsed.pluginId}/${parsed.panelId}`,
    };
  }
  if (parsed.kind === "verse-translated") {
    return {
      id: verseTranslatedTabId(parsed.path, parsed.lang),
      kind: "verse-translated",
      name: title || basename(parsed.path) || "Translation",
      path: parsed.path,
    };
  }
  const path = parsed.path.replace(/\\/g, "/");
  const name = isVerseFile(path) ? basename(path) : title || path.split("/").pop() || path;
  return { id: fileTabId(path), kind: "file", name, path };
}

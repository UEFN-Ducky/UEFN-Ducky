import { useCallback, useEffect, useMemo, useRef } from "react";

import { getApi } from "./usePanelApi";

import { parseFocusId, focusIdToEditorTab, focusActivateNeedsOpen, decodeFocusParam } from "./useFocusWindow";

import { chatTabId, fileTabId, terminalTabId, type EditorLayoutState, type EditorTab } from "../types/panel";

import type { TerminalShell } from "../terminal/types";

import { basename, isVerseFile, projectRelativePath } from "../verse-editor/utils/isVerseFile";

import { onShownChatsChange, shownChatIds } from "./shownChats";
import { WINDOW_ID, installFileDeleteListener, installFileRenameListener, installTabRegistryListener, openOrFocusTab, releaseAllTabs, reportOpenTabs, reportOpenTabsNow } from "../tabs/tabRegistryClient";
import { registerUsageTabFocusIfOpen } from "../navigation/openUsageTab";
import { clearBrowserPaneBounds, orphanedPluginTabs } from "../plugin-ui";
import { usePluginContributions } from "./usePluginContributions";

import { setExternalTabDropHandler } from "../utils/editorTabDrag";

import { useEditorLayout } from "./useEditorLayout";

import { createDefaultLayout } from "../utils/editorLayoutOps";

import { useTerminalTabs } from "../terminal/useTerminalTabs";

import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { useAppearance } from "../theme/AppearanceContext";



export interface FocusTabEvent {

  focus_id: string;

  title: string;

}



export interface FocusLayoutRestoreEvent {
  layout: EditorLayoutState;
}



const LAYOUT_DEBOUNCE_MS = 300;



function notifyMainFocusTabActive(focusId: string, title: string): void {
  void getApi()?.notify_focus_tab_active(focusId, title);
}

/** Window methods newer than the typed panel API (Chat API steps 003a08f5f6, 61ee7f6545). */
type WindowViewApi = {
  set_visible_chats?: (ids: string[], viewer_id: string) => Promise<unknown>;
  get_restore_tabs?: (window_id: string) => Promise<string[] | null | undefined>;
};
const viewApi = () => getApi() as unknown as WindowViewApi | null;

/** The chats on screen: each editor group's (pane's) active tab when it is a chat. */
export function visibleChatIds(layout: EditorLayoutState, tabs: readonly EditorTab[]): string[] {
  const byId = new Map(tabs.map((t) => [t.id, t]));
  const out = new Set<string>();
  for (const group of Object.values(layout.groups || {})) {
    const tab = group?.activeTabId ? byId.get(group.activeTabId) : undefined;
    if (tab?.kind === "chat" && tab.chatId) out.add(tab.chatId);
  }
  return [...out].sort();
}

const lastVisibleKey = new Map<string, string>();
const shownByViewer = new Map<string, string[]>();
let visibilityListener = false;

function sendVisible(viewerId: string): void {
  // The viewer's tabs plus every chat a visible pane shows outside them (a plugin's chat
  // dock, a pop-up card): hooks/shownChats.ts.
  const shown = [...new Set([...(shownByViewer.get(viewerId) ?? []), ...shownChatIds()])].sort();
  const ids = typeof document !== "undefined" && document.hidden ? [] : shown;
  const key = ids.join("\0");
  if (lastVisibleKey.get(viewerId) === key) return;
  lastVisibleKey.set(viewerId, key);
  void viewApi()?.set_visible_chats?.(ids, viewerId);
}

/**
 * Tell the app which chats this viewer shows, so live text streams only to them. Sends only
 * when the set changes (never on a timer); a hidden or minimised window shows none.
 * The main window and the phone/remote views call it too, with their own viewer id.
 */
export function reportVisibleChats(ids: readonly string[], viewerId: string = WINDOW_ID): void {
  shownByViewer.set(viewerId, [...new Set(ids)].sort());
  listenForShownChanges();
  sendVisible(viewerId);
}

function listenForShownChanges(): void {
  if (visibilityListener || typeof document === "undefined") return;
  visibilityListener = true;
  const resend = () => {
    for (const viewer of shownByViewer.keys()) sendVisible(viewer);
  };
  document.addEventListener("visibilitychange", resend);
  // A pane outside the tabs opened or closed: this window's own list changes with it.
  onShownChatsChange(() => {
    if (!shownByViewer.has(WINDOW_ID)) shownByViewer.set(WINDOW_ID, []);
    resend();
  });
}

/** Start reporting chats that visible panes show, before any tab layout reports (ChatPane). */
export function watchShownChats(): void {
  listenForShownChanges();
}

/** Report this window's visible chats whenever the set changes (a re-render alone sends nothing). */
export function useReportVisibleChats(layout: EditorLayoutState, tabs: readonly EditorTab[], viewerId: string = WINDOW_ID): void {
  const key = useMemo(() => visibleChatIds(layout, tabs).join("\0"), [layout, tabs]);
  useEffect(() => reportVisibleChats(key ? key.split("\0") : [], viewerId), [key, viewerId]);
}

/**
 * After a crash reload, reopen the tabs this window had, in order (once per page load; a normal
 * load gets [] and does nothing). Returns the tab ids it opened.
 */
export async function restoreTabsAfterCrash(open: (tabId: string) => void, windowId: string = WINDOW_ID): Promise<string[]> {
  let tabs: string[] = [];
  try {
    tabs = (await viewApi()?.get_restore_tabs?.(windowId)) || [];
  } catch {
    return [];
  }
  const ids = tabs.filter((id): id is string => typeof id === "string" && id.length > 0);
  for (const id of ids) open(id);
  return ids;
}

/** Test helper. */
export function _resetVisibleChatsForTests(): void {
  lastVisibleKey.clear();
  shownByViewer.clear();
}


export function useFocusWindowTabs(initialFocusId: string, initialTitle: string) {
  const birthFocusId = decodeFocusParam(initialFocusId);

  const initialTab = focusIdToEditorTab(birthFocusId, initialTitle);

  const editor = useEditorLayout(initialTab ? [initialTab] : []);

  const editorRef = useRef(editor);

  editorRef.current = editor;

  const { restartTerminalTab, spawnAndOpen, parkTerminalTab, killTerminalTab, reopenTerminalTab, parkedTabs } =
    useTerminalTabs(editor);

  const { confirm } = useConfirmModal();
  const { guardUnsavedChanges } = useAppearance();
  const pluginContrib = usePluginContributions();

  const birthTabIdRef = useRef<string | null>(initialTab?.id ?? null);
  if (birthTabIdRef.current === null && editor.openTabs.length > 0) {
    birthTabIdRef.current = editor.openTabs[0]!.id;
  }

  // Tell main which tab this focus window opened with — Discord dock visibility
  // (and sidebar sync) key off this, and it used to miss the birth tab.
  useEffect(() => {
    const tab = focusIdToEditorTab(birthFocusId, initialTitle);
    if (!tab) return;
    notifyMainFocusTabActive(tab.id, tab.name || initialTitle);
  }, [birthFocusId, initialTitle]);

  const openTabsRef = useRef(editor.openTabs);
  openTabsRef.current = editor.openTabs;

  useReportVisibleChats(editor.layout, editor.openTabs);

  useEffect(() => {
    const defer = (fn: () => void) => window.setTimeout(fn, 0);

    window.__uefnFocusRestoreLayout = (event: FocusLayoutRestoreEvent) => {
      defer(() => {
        const tabs = openTabsRef.current;
        if (!event?.layout || tabs.length === 0) return;
        editorRef.current.initLayoutState(tabs, event.layout);
      });
    };

    return () => {
      delete window.__uefnFocusRestoreLayout;
    };
  }, []);

  useEffect(() => {
    const birthTabId = birthTabIdRef.current;
    if (!birthTabId || editor.openTabs.length === 0) return;
    const timer = window.setTimeout(() => {
      void getApi()?.report_focus_window_layout(birthTabId, editor.layout);
    }, LAYOUT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [editor.layout, editor.openTabs.length]);



  useEffect(() => {

    const parsed = parseFocusId(birthFocusId);

    if (parsed?.kind !== "terminal") return;

    let cancelled = false;

    void (async () => {

      const api = getApi();

      if (!api) return;

      const list = await api.terminal_list();

      if (cancelled) return;

      const session = list.sessions.find((s) => String(s.session_id) === parsed.sessionId);

      if (!session) return;

      const tab: EditorTab = {

        id: terminalTabId(parsed.sessionId),

        kind: "terminal",

        name: String(session.title || initialTitle || "terminal"),

        terminalSessionId: parsed.sessionId,

        terminalShell: (String(session.shell || "bash") as TerminalShell),

        terminalWsUrl: String(session.ws_url || ""),

        terminalCwd: String(session.cwd || ""),

      };

      editorRef.current.initLayoutState([tab], createDefaultLayout([tab.id]));

    })();

    return () => {

      cancelled = true;

    };

  }, [birthFocusId, initialTitle]);



  openTabsRef.current = editor.openTabs;



  const hydrateTerminalTab = useCallback(async (sessionId: string, title: string) => {

    const api = getApi();

    if (!api) return;

    const list = await api.terminal_list();

    const session = list.sessions.find((s) => String(s.session_id) === sessionId);

    if (!session) return;

    const tab: EditorTab = {

      id: terminalTabId(sessionId),

      kind: "terminal",

      name: String(session.title || title || "terminal"),

      terminalSessionId: sessionId,

      terminalShell: (String(session.shell || "bash") as TerminalShell),

      terminalWsUrl: String(session.ws_url || ""),

      terminalCwd: String(session.cwd || ""),

    };

    editorRef.current.openTab(tab);

  }, []);



  const addTab = useCallback((focusId: string, title: string) => {

    const parsed = parseFocusId(focusId);

    if (parsed?.kind === "terminal") {

      void hydrateTerminalTab(parsed.sessionId, title);

      return;

    }

    const tab = focusIdToEditorTab(focusId, title);

    if (!tab) return;

    editorRef.current.openTab(tab);
    notifyMainFocusTabActive(focusId, title);

  }, [hydrateTerminalTab]);



  // A crash reload (WebView2 process died) reopens the tabs this window had, once.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    void restoreTabsAfterCrash((tabId) => addTab(tabId, ""));
  }, [addTab]);

  const activateTab = useCallback((focusId: string, title = "") => {
    // Ghost ownership: Python still lists the tab but React already dropped it.
    // Activate alone only touches layout — re-open when missing.
    if (focusActivateNeedsOpen(openTabsRef.current.map((t) => t.id), focusId)) {
      addTab(focusId, title);
      return;
    }
    const tab = focusIdToEditorTab(focusId, title);
    if (!tab) return;
    editorRef.current.restoreTabToLayout(tab.id);
    notifyMainFocusTabActive(focusId, tab.name || title);
  }, [addTab]);



  const removeTab = useCallback((tabId: string) => {

    editorRef.current.closeTabInLayout(tabId);

  }, []);



  useEffect(() => {

    const defer = (fn: () => void) => window.setTimeout(fn, 0);



    window.__uefnFocusTabOpen = (event: FocusTabEvent) => {

      defer(() => addTab(event.focus_id, event.title));

    };



    window.__uefnFocusTabActivate = (event: FocusTabEvent) => {

      defer(() => activateTab(event.focus_id, event.title));

    };



    window.__uefnFocusTabClose = (event: FocusTabEvent) => {

      defer(() => removeTab(event.focus_id));

    };



    return () => {

      delete window.__uefnFocusTabOpen;

      delete window.__uefnFocusTabActivate;

      delete window.__uefnFocusTabClose;

    };

  }, [activateTab, addTab, removeTab]);



  const closeTab = useCallback(
    async (tabId: string) => {
      const api = getApi();
      const closing = openTabsRef.current.find((t) => t.id === tabId);

      if (closing?.kind === "settings") {
        if (!(await guardUnsavedChanges())) return;
      }

      if (closing?.kind === "terminal" && closing.terminalSessionId) {
        // Hide only — shell stays alive for reopen from the header list (main or here).
        parkTerminalTab(closing);
        const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
        editorRef.current.closeTabInLayout(tabId);
        reportOpenTabsNow(remaining.map((t) => t.id));
        if (remaining.length === 0) {
          releaseAllTabs();
          if (api) await api.close_this_window("last terminal tab hidden", WINDOW_ID);
        } else if (api) {
          await api.close_focus_window(tabId, "terminal tab hidden", WINDOW_ID);
        }
        return;
      }

      // Anything except terminals returns to main (or primary focus group).
      if (closing && closing.kind !== "terminal") {
        const returned = await api?.return_tab_to_main(tabId, closing.name, WINDOW_ID);
        if (returned) return;
      }

      const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
      editorRef.current.closeTabInLayout(tabId);
      reportOpenTabsNow(remaining.map((t) => t.id));
      if (remaining.length === 0) {
        releaseAllTabs();
        if (api) await api.close_this_window("last tab closed", WINDOW_ID);
        return;
      }
      if (api) await api.close_focus_window(tabId, "tab closed", WINDOW_ID);
    },
    [parkTerminalTab, guardUnsavedChanges],
  );

  const killTerminal = useCallback(
    async (tabId: string) => {
      const tab =
        openTabsRef.current.find((t) => t.id === tabId) ?? parkedTabs.find((t) => t.id === tabId);
      if (!tab || tab.kind !== "terminal") return;
      if (tab.terminalSessionId) {
        const state = await getApi()?.terminal_busy(tab.terminalSessionId).catch(() => null);
        if (state?.running) {
          const ok = await confirm({
            title: "Delete terminal?",
            message:
              "A command is still running in this terminal. Deleting it will kill the process and everything it started.",
            confirmLabel: "Kill & delete",
            danger: true,
          });
          if (!ok) return;
        }
      }
      const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
      reportOpenTabsNow(remaining.map((t) => t.id));
      await killTerminalTab(tab);
      if (remaining.length === 0) {
        releaseAllTabs();
        await getApi()?.close_this_window("last terminal killed", WINDOW_ID);
      }
    },
    [parkedTabs, killTerminalTab, confirm],
  );



  const openFileTab = useCallback((path: string, name: string) => {

    const norm = projectRelativePath(path);

    const tabId = fileTabId(norm);

    const tabName = isVerseFile(norm) ? basename(norm) : name;

    // VS Code single-tab rule: focus the window that owns this tab (main or another
    // focus window); open here only when nobody has it.
    void openOrFocusTab(tabId, () =>
      editorRef.current.openTab({ id: tabId, kind: "file", name: tabName, path: norm }, { activate: true }),
    );

  }, []);



  const openChatTab = useCallback((chat: { id: string; name: string; duckyStyle?: string }) => {

    const tabId = chatTabId(chat.id);

    void openOrFocusTab(tabId, () =>
      editorRef.current.openTab({
        id: tabId,
        kind: "chat",
        name: chat.name,
        chatId: chat.id,
        duckyStyle: chat.duckyStyle,
      }),
    );

  }, []);

  // Cross-window tab registry: report our tabs; close local copies claimed elsewhere
  // (drag = move); activate tabs on focus requests targeting this window.
  // Only a different tab list is reported (a tab's rename or re-render gives a new array, same ids).
  const openTabIdsKey = editor.openTabs.map((t) => t.id).join("\0");
  useEffect(() => {
    reportOpenTabs(openTabIdsKey ? openTabIdsKey.split("\0") : []);
  }, [openTabIdsKey]);

  // Ignore cross-window claims briefly after birth — workspace restore / main
  // re-claim races were destroying the OS window the moment it opened.
  const mountedAtRef = useRef(Date.now());

  useEffect(() => {
    return installTabRegistryListener(
      (tabId) => openTabsRef.current.some((t) => t.id === tabId),
      (tabId, by) => {
        if (Date.now() - mountedAtRef.current < 2500) {
          return;
        }
        const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
        editorRef.current.closeTabInLayout(tabId);
        // Keep Python group.tabs in sync — otherwise the next Focus short-circuits to
        // Activate on a ghost id and the window stays empty ("No tabs open.").
        // close_focus_window destroys the OS window when this was the last tab.
        void getApi()?.close_focus_window(tabId, `tab claimed by ${by}`, WINDOW_ID);
        if (remaining.length === 0) {
          releaseAllTabs();
        }
      },
      (tabId) => {
        const tab = openTabsRef.current.find((t) => t.id === tabId);
        if (tab) editorRef.current.openTab(tab, { activate: true });
      },
    );
  }, []);

  useEffect(() => {
    return registerUsageTabFocusIfOpen((tabId) => {
      const tab = openTabsRef.current.find((t) => t.id === tabId);
      if (!tab) return false;
      editorRef.current.openTab(tab, { activate: true });
      return true;
    });
  }, []);

  // Tab dropped here from ANOTHER window: open + activate it (the registry claim
  // closes the source copy). Tell the host too, or its group never learns the tab
  // moved — closing it would not hand it back to main, and a restart would lose it.
  useEffect(() => {
    return setExternalTabDropHandler((tab) => {
      editorRef.current.openTab(tab, { activate: true });
      void getApi()?.adopt_tab_into_this_focus_window(tab.id, tab.name, WINDOW_ID);
    });
  }, []);

  // File renamed: rename the tab in place (VS Code behavior), never orphan it.
  useEffect(() => {
    return installFileRenameListener((oldId, newId, newPath) => {
      if (!openTabsRef.current.some((t) => t.id === oldId)) return;
      editorRef.current.remapTabId(oldId, newId);
      editorRef.current.setOpenTabs((prev) =>
        prev.map((t) => (t.id === newId ? { ...t, path: newPath, name: basename(newPath) } : t)),
      );
    });
  }, []);

  // File (or folder) deleted: close its tab(s). Leaving the tab open keeps its
  // verse-lsp doc alive, resurrecting the stale Problems entries. If the delete
  // empties this focus window, close the window too (same rule as the last tab
  // being claimed away by another window).
  useEffect(() => {
    return installFileDeleteListener((deletedId) => {
      const doomed = openTabsRef.current.filter(
        (t) => t.kind === "file" && (t.id === deletedId || t.id.startsWith(`${deletedId}/`)),
      );
      if (doomed.length === 0) return;
      for (const t of doomed) editorRef.current.closeTabInLayout(t.id);
      const doomedIds = new Set(doomed.map((t) => t.id));
      const remaining = openTabsRef.current.filter((t) => !doomedIds.has(t.id));
      reportOpenTabsNow(remaining.map((t) => t.id));
      if (remaining.length === 0) {
        releaseAllTabs();
        void getApi()?.close_this_window("file deleted", WINDOW_ID);
      }
    });
  }, []);

  // Plugin disabled / uninstalled: close that plugin's tabs here too. If the
  // focus window was only that plugin (e.g. pop-out Browser), close the window.
  useEffect(() => {
    if (!pluginContrib.ready) return;
    const doomed = orphanedPluginTabs(openTabsRef.current, pluginContrib.enabled_ids);
    if (doomed.length === 0) return;
    for (const t of doomed) {
      if (t.kind === "plugin") {
        clearBrowserPaneBounds(t.id);
        void getApi()?.browser_pane_close?.(t.id);
      }
      editorRef.current.closeTabInLayout(t.id);
    }
    const doomedIds = new Set(doomed.map((t) => t.id));
    const remaining = openTabsRef.current.filter((t) => !doomedIds.has(t.id));
    reportOpenTabsNow(remaining.map((t) => t.id));
    if (remaining.length === 0) {
      releaseAllTabs();
      void getApi()?.close_this_window(`plugin disabled: ${doomed.map((t) => t.id).join(",")}`, WINDOW_ID);
    }
  }, [pluginContrib.ready, pluginContrib.enabled_ids, editor.openTabs]);



  return {

    ...editor,

    closeTab,

    killTerminal,

    reopenTerminalTab,

    parkedTabs,

    openFileTab,

    openChatTab,

    notifyMainFocusTabActive,

    restartTerminalTab,

    spawnAndOpen,

  };

}


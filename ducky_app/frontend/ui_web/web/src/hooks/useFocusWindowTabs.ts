import { useCallback, useEffect, useRef } from "react";

import { getApi } from "./usePanelApi";

import { parseFocusId, focusIdToEditorTab, focusActivateNeedsOpen, decodeFocusParam } from "./useFocusWindow";

import { chatTabId, fileTabId, terminalTabId, type EditorLayoutState, type EditorTab } from "../types/panel";

import type { TerminalShell } from "../terminal/types";

import { basename, isVerseFile, projectRelativePath } from "../verse-editor/utils/isVerseFile";

import { installFileDeleteListener, installFileRenameListener, installTabRegistryListener, openOrFocusTab, releaseAllTabs, reportOpenTabs, reportOpenTabsNow } from "../tabs/tabRegistryClient";
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
          if (api) await api.close_this_window();
        } else if (api) {
          await api.close_focus_window(tabId);
        }
        return;
      }

      // Anything except terminals returns to main (or primary focus group).
      if (closing && closing.kind !== "terminal") {
        const returned = await api?.return_tab_to_main(tabId, closing.name);
        if (returned) return;
      }

      const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
      editorRef.current.closeTabInLayout(tabId);
      reportOpenTabsNow(remaining.map((t) => t.id));
      if (remaining.length === 0) {
        releaseAllTabs();
        if (api) await api.close_this_window();
        return;
      }
      if (api) await api.close_focus_window(tabId);
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
        await getApi()?.close_this_window();
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
  useEffect(() => {
    reportOpenTabs(editor.openTabs.map((t) => t.id));
  }, [editor.openTabs]);

  // Ignore cross-window claims briefly after birth — workspace restore / main
  // re-claim races were destroying the OS window the moment it opened.
  const mountedAtRef = useRef(Date.now());

  useEffect(() => {
    return installTabRegistryListener(
      (tabId) => openTabsRef.current.some((t) => t.id === tabId),
      (tabId) => {
        if (Date.now() - mountedAtRef.current < 2500) {
          return;
        }
        const remaining = openTabsRef.current.filter((t) => t.id !== tabId);
        editorRef.current.closeTabInLayout(tabId);
        // Keep Python group.tabs in sync — otherwise the next Focus short-circuits to
        // Activate on a ghost id and the window stays empty ("No tabs open.").
        // close_focus_window destroys the OS window when this was the last tab.
        void getApi()?.close_focus_window(tabId);
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
      void getApi()?.adopt_tab_into_this_focus_window(tab.id, tab.name);
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
        void getApi()?.close_this_window();
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
      void getApi()?.close_this_window();
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


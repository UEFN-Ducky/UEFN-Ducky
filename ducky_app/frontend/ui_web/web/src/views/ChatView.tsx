import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type ChatSidebarHandle } from "../components/ChatSidebar";
import { WorkspaceDockLayout } from "../workspace/WorkspaceDockLayout";
import { WorkspaceDockProvider } from "../workspace/WorkspaceDockContext";
import { ChatEmptyState } from "../components/ChatEmptyState";
import { EditorTabDropSurface } from "../components/EditorTabDropSurface";
import { ChatHeaderBridge } from "../components/ChatHeaderBridge";
import { VerseWorkflowBridge } from "../components/VerseWorkflowBridge";
import { DuckyProfileModal, type DuckyEditTarget, type DuckyProfileModalMode } from "../components/ducky/DuckyProfileModal";
import { useDuckyCatalog } from "../components/ducky/DuckyCatalogContext";
import { requestOpenDuckyEditor } from "../navigation/openDuckyEditor";
import { onDuckyProfileChanged } from "../navigation/duckyProfileChanged";
import { registerOpenDuckyProfileTab } from "../navigation/openDuckyProfileTab";
import { SplitEditorLayout } from "../components/SplitEditorLayout";
import { UnsavedChangesModal } from "../components/UnsavedChangesModal";
import { useChatFolders } from "../hooks/useChatFolders";
import { ARCHIVE_FOLDER_ID } from "../utils/archiveFolder";
import { getApi } from "../hooks/usePanelApi";
import { useAgentEventBus } from "../hooks/useAgentEventBus";
import { useRunningAgents } from "../hooks/useRunningAgents";
import { useChatsChanged } from "../hooks/useChatsChanged";
import { parseFocusId, focusIdToEditorTab, useFocusWindow } from "../hooks/useFocusWindow";
import { useEditorWorkspace } from "../hooks/useEditorWorkspace";
import { useEditorLayout } from "../hooks/useEditorLayout";
import {
  dismissCompletionAlert,
  useChatCompletionAlerts,
  useCompletionAlertChatIds,
} from "../hooks/useChatCompletionAlerts";
import { useSpokenReplies } from "../voice/useSpokenReplies";
import { useRegisterEditorWorkspaceFlush } from "../contexts/EditorWorkspaceBridge";
import { useRegisterQuickOpenHandlers, type QuickOpenHandlers } from "../contexts/QuickOpenBridge";
import { useRegisterAskAiHandlers, type AskAiPayload } from "../contexts/AskAiBridge";
import { useRegisterProblemsDuckyHandlers } from "../contexts/ProblemsDuckyBridge";
import { useProblemsToDuckyHandlers } from "../hooks/useProblemsToDuckyHandlers";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import {
  useNavigationHistoryOptional,
  type NavLocation,
} from "../navigation/NavigationHistoryContext";
import type { ChatLayoutMode, ChatTab, EditorDropZone, EditorTab, FolderItem } from "../types/panel";
import {
  chatTabId,
  duckyProfileTabId,
  fileTabId,
  planTabId,
  pluginUiTabId,
  settingsTabId,
  usageTabId,
} from "../types/panel";
import { registerOpenSettingsEditorTab } from "../navigation/openSettingsTab";
import { emitAppHook } from "../sfx/appHooks";
import { registerOpenDiscordTab, setDiscordTabOpen } from "../navigation/openDiscordTab";
import { registerOpenPlanTab, type OpenPlanRequest } from "../navigation/openPlanTab";
import {
  registerOpenUsageTab,
  registerUsageTabFocusIfOpen,
  type OpenUsageRequest,
} from "../navigation/openUsageTab";
import {
  openVerseTranslatedTab,
  readTranslationUiLang,
  registerOpenVerseTranslatedTab,
  verseTranslatedTabId,
} from "../navigation/openVerseTranslatedTab";
import { registerOpenTranslatedChat } from "../navigation/openTranslatedChat";
import { isAutoTranslateFile } from "../navigation/tabTranslatePrefs";
import { isEnglishLang } from "./settings/translationLanguages";
import {
  clearBrowserPaneBounds,
  clearBoundDucktactoeChat,
  orphanedPluginTabs,
  parsePluginUiTabId,
  pluginUiInstanceTabId,
  registerOpenPluginUiTab,
  requestOpenPluginUiTab,
  setBrowserPaneInitialUrl,
  shouldSuppressRemoteChatOpen,
} from "../plugin-ui";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installFileDeleteListener, installFileRenameListener, installTabRegistryListener, openOrFocusTab, reportOpenTabs, reportOpenTabsNow } from "../tabs/tabRegistryClient";
import { useAppearance } from "../theme/AppearanceContext";
import { setExternalTabDropHandler } from "../utils/editorTabDrag";
import { DEFAULT_TERMINAL_SHELL, type TerminalShell } from "../terminal/types";
import { TerminalHeaderBridge } from "../components/TerminalHeaderBridge";
import { getTerminalsEnabled } from "../contexts/TerminalsSettingsContext";
import { useTerminalTabs } from "../terminal/useTerminalTabs";
import { basename, isVerseFile, projectRelativePath } from "../verse-editor/utils/isVerseFile";
import { openSidebarProjectFile } from "../utils/openSidebarProjectFile";
import { focusActivatedEditorTab, openProjectFileAt } from "../utils/openProjectFileAt";
import { collectActiveTabIds, collectTabIds, findGroupForTab } from "../utils/editorLayoutOps";
import { formatAskDraft } from "../utils/formatAskDraft";
import { openChatBesideFile } from "../utils/openChatBesideFile";
import { enqueueComposerDraft } from "../hooks/chatComposerCache";
import { syncAskAiMenuItems } from "../verse-editor/monaco/registerAskAiContextMenu";
import { getFollowCodeSettings } from "../verse-editor/queue/followCodeSettings";
import { VerseEditorProvider, useVerseEditorOptional } from "../verse-editor";
import { useUiTarget } from "../ui-targets/registry";

function flattenChats(folders: FolderItem[], rootChats: FolderItem["chats"] = []): ChatTab[] {
  const out: ChatTab[] = [...rootChats];
  const walk = (items: FolderItem[]) => {
    for (const folder of items) {
      out.push(...folder.chats);
      walk(folder.children);
    }
  };
  walk(folders);
  return out;
}

interface ChatViewProps {
  layoutMode: ChatLayoutMode;
  sidebarRefresh: number;
  projectSlug: string;
  projectPath: string;
}

export function ChatView(props: ChatViewProps) {
  useAgentEventBus();
  return <ChatViewBody {...props} />;
}

function ChatViewBody({ layoutMode, sidebarRefresh, projectSlug, projectPath }: ChatViewProps) {
  const { confirm } = useConfirmModal();
  const { guardUnsavedChanges } = useAppearance();
  const { openFocus, openFocusAtPoint } = useFocusWindow();
  const editor = useEditorLayout();
  const {
    openTabs,
    setOpenTabs,
    layout,
    setLayout,
    openTab,
    promoteTab,
    closeTabInLayout,
    activateTabInGroup,
    setFocusedGroup,
    reorderTabsInGroup,
    dropTabOnGroup,
    remapTabId,
    initLayoutState,
    splitFocusedGroupWithTab,
    openTabBeside,
    openTabInZone,
    openTabsRef,
    toggleGroupLockInLayout,
  } = editor;

  // Ref mirror so agent-open callbacks stay identity-stable (the provider rebuilds
  // the action queue whenever its callbacks change — don't churn it per layout change).
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  const {
    folders,
    setFolders,
    rootChats,
    setRootChats,
    hubChats,
    archiveChats,
    setArchiveChats,
    load,
    foldersLoaded,
  } = useChatFolders(sidebarRefresh);
  const runningChatIds = useRunningAgents();
  // Brand-new duckies open before list_all_conversations returns — keep them in
  // allChats so ChatPane gets model/codingAgent on first paint (sidebar uses folders).
  const [pendingChats, setPendingChats] = useState<ChatTab[]>([]);
  // Hub chats are hidden from the folder tree but must stay in allChats so group
  // panes resolve isGroup / GroupMemberStrip (folder click opens the hub).
  const allChats = useMemo(() => {
    const base = [...flattenChats(folders, rootChats), ...hubChats];
    const seen = new Set(base.map((c) => c.id));
    return [...base, ...pendingChats.filter((c) => !seen.has(c.id))];
  }, [folders, rootChats, hubChats, pendingChats]);

  const openChatTab = useCallback(
    (chat: ChatTab, options?: { preview?: boolean }) => {
      dismissCompletionAlert(chat.id);
      const id = chatTabId(chat.id);
      // A permanent open (double-click) pins a tab that is already open as a preview,
      // even when the registry raises it in another window instead of reopening here.
      if (!options?.preview) promoteTab(id);
      void openOrFocusTab(id, () =>
        openTab(
          {
            id,
            kind: "chat",
            name: chat.name,
            chatId: chat.id,
            duckyStyle: chat.duckyStyle,
            isGroup: Boolean(chat.isGroup),
          },
          options,
        ),
      );
      emitAppHook("agent.selected", { chatId: chat.id });
    },
    [openTab, promoteTab],
  );

  const openFileTab = useCallback(
    (path: string, name: string, options?: { activate?: boolean; preview?: boolean }) => {
      const norm = projectRelativePath(path);
      const id = fileTabId(norm);
      const tabName = isVerseFile(norm) ? basename(norm) : name;
      if (!options?.preview) promoteTab(id);
      // VS Code single-tab rule: focus the window that owns this tab, wherever it
      // lives; open here (and claim) only when no other window has it.
      void openOrFocusTab(id, () =>
        openTab({ id, kind: "file", name: tabName, path: norm }, options),
      );
      // Per-file hover "Auto translate" — open read-only translated view too.
      if (isAutoTranslateFile(norm) && !isEnglishLang(readTranslationUiLang())) {
        openVerseTranslatedTab(norm);
      }
    },
    [openTab, promoteTab],
  );

  const openPlanTab = useCallback(
    (chatId: string, title?: string, projectRoot?: string) => {
      const id = planTabId(chatId);
      const name = (title || "Plan").trim() || "Plan";
      void openOrFocusTab(id, () =>
        openTab(
          {
            id,
            kind: "plan",
            name,
            chatId,
            ...(projectRoot !== undefined ? { projectRoot } : {}),
          },
          { activate: true },
        ),
      );
    },
    [openTab],
  );

  useEffect(() => {
    return registerOpenPlanTab((req: OpenPlanRequest) => {
      openPlanTab(req.chatId, req.title, req.projectRoot);
    });
  }, [openPlanTab]);

  const openUsageTab = useCallback(
    (providerId: string, label?: string) => {
      const id = usageTabId(providerId);
      const name = `${(label || providerId).trim() || "Provider"} usage`;
      void openOrFocusTab(id, () =>
        openTab(
          {
            id,
            kind: "usage",
            name,
            path: providerId.trim().toLowerCase(),
          },
          { activate: true },
        ),
      );
    },
    [openTab],
  );

  useEffect(() => {
    return registerOpenUsageTab((req: OpenUsageRequest) => {
      openUsageTab(req.providerId, req.label);
    });
  }, [openUsageTab]);

  useEffect(() => {
    return registerUsageTabFocusIfOpen((tabId) => {
      const tab = openTabsRef.current.find((t) => t.id === tabId);
      if (!tab) return false;
      openTab(tab, { activate: true });
      return true;
    });
  }, [openTab, openTabsRef]);

  const openSettingsTab = useCallback(() => {
    const id = settingsTabId();
    void openOrFocusTab(id, () =>
      openTab({ id, kind: "settings", name: "Settings" }, { activate: true }),
    );
  }, [openTab]);

  useEffect(() => {
    return registerOpenSettingsEditorTab(openSettingsTab);
  }, [openSettingsTab]);

  const openDuckyProfileTab = useCallback(
    (req: { profileId: string; name: string; duckyStyle?: string }) => {
      const id = duckyProfileTabId(req.profileId);
      void openOrFocusTab(id, () =>
        openTab(
          {
            id,
            kind: "ducky-profile",
            name: req.name || "Ducky",
            path: req.profileId,
            duckyStyle: req.duckyStyle,
          },
          { activate: true },
        ),
      );
    },
    [openTab],
  );

  useEffect(() => registerOpenDuckyProfileTab(openDuckyProfileTab), [openDuckyProfileTab]);

  // Keep pop-out ducky-profile tabs titled / closed when the library changes.
  useEffect(() => {
    return onDuckyProfileChanged((ev) => {
      const tabId = duckyProfileTabId(ev.profileId);
      if (ev.type === "deleted") {
        if (openTabsRef.current.some((t) => t.id === tabId)) {
          closeTabInLayout(tabId);
        }
        return;
      }
      if (!ev.name && !ev.duckyStyle) return;
      setOpenTabs((prev) =>
        prev.map((t) =>
          t.id === tabId
            ? {
                ...t,
                name: ev.name || t.name,
                duckyStyle: ev.duckyStyle ?? t.duckyStyle,
              }
            : t,
        ),
      );
    });
  }, [closeTabInLayout, setOpenTabs, openTabsRef]);

  const openVerseTranslated = useCallback(
    (relativePath: string) => {
      const lang = readTranslationUiLang();
      if (isEnglishLang(lang)) return;
      const norm = relativePath.replace(/\\/g, "/");
      const id = verseTranslatedTabId(norm, lang);
      const name = `${basename(norm)} (${lang})`;
      void openOrFocusTab(id, () =>
        openTab({ id, kind: "verse-translated", name, path: norm }, { activate: true }),
      );
    },
    [openTab],
  );
  useEffect(() => registerOpenVerseTranslatedTab(openVerseTranslated), [openVerseTranslated]);
  useEffect(() => registerOpenTranslatedChat(openChatTab), [openChatTab]);

  const pluginContrib = usePluginContributions();
  const openPluginUiTab = useCallback(
    (pluginId: string, panelId: string, title?: string) => {
      // Duck-Tac-Toe: open the branded chat (board lives as an aside inside chat).
      if (
        pluginId.trim().toLowerCase() === "ducktactoe" &&
        panelId.trim().toLowerCase() === "board"
      ) {
        void (async () => {
          const { ensureDucktactoeGameChat } = await import("../plugin-ui/ducktactoeBoardChat");
          const chat = await ensureDucktactoeGameChat(allChats);
          if (chat) openChatTab(chat);
        })();
        return;
      }
      const id = pluginUiTabId(pluginId, panelId);
      const panel = pluginContrib.ui_panels.find(
        (p) => p.plugin_id === pluginId && p.id === panelId,
      );
      const name = title || panel?.title || panelId;
      void openOrFocusTab(id, () =>
        openTab({ id, kind: "plugin", name }, { activate: true }),
      );
    },
    [openTab, openChatTab, pluginContrib.ui_panels, allChats],
  );
  useEffect(() => registerOpenPluginUiTab(openPluginUiTab), [openPluginUiTab]);

  const openDiscordTab = useCallback(
    (_botId?: string, _label?: string) => {
      requestOpenPluginUiTab("discord", "discord-chat");
    },
    [],
  );
  useEffect(() => registerOpenDiscordTab(openDiscordTab), [openDiscordTab]);

  // "Open link in new tab" from a native browser pane: open a fresh instance tab
  // of the same plugin panel whose pane starts at the requested URL.
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "browser_pane_new_window") return;
      const source = parsePluginUiTabId(event.pane_id ?? "");
      const url = event.url ?? "";
      if (!source || !url) return;
      const panel = pluginContrib.ui_panels.find(
        (p) => p.plugin_id === source.pluginId && p.id === source.panelId,
      );
      const id = pluginUiInstanceTabId(source.pluginId, source.panelId);
      setBrowserPaneInitialUrl(id, url);
      openTab({ id, kind: "plugin", name: panel?.title || source.panelId }, { activate: true });
    });
  }, [openTab, pluginContrib.ui_panels]);

  const isDiscordPluginTabId = (id: string) =>
    id === "plugin:discord:discord-chat" || id.startsWith("plugin:discord:");

  // While Discord is open as a tab HERE or in any focus window, hide the dock
  // panel. Detaching to a focus window used to flip this off (main tabs only),
  // so the sidebar Discord panel "came back" the moment the editor tab left.
  const refreshDiscordOccupied = useCallback(() => {
    const inMain = openTabsRef.current.some((t) => isDiscordPluginTabId(t.id));
    if (inMain) {
      setDiscordTabOpen(true);
      return;
    }
    const api = getApi();
    if (!api?.list_focus_tab_ids) {
      setDiscordTabOpen(false);
      return;
    }
    void api.list_focus_tab_ids().then((ids) => {
      const occupied = (ids ?? []).some((id) => isDiscordPluginTabId(id));
      setDiscordTabOpen(occupied);
    });
  }, [openTabsRef]);

  useEffect(() => {
    refreshDiscordOccupied();
  }, [openTabs, refreshDiscordOccupied]);
  useEffect(() => () => setDiscordTabOpen(false), []);

  // A file dragged in from Explorer and dropped on the editor: open it editable in
  // place (ext: path) from its on-disk location, saved straight back — never imported.
  // Bypasses projectRelativePath so the ext: prefix is preserved. The identity is
  // lowercased (Windows FS is case-insensitive; backend re-resolves real case) so the
  // tab, dirty dot, and save-on-close tracking all agree; display keeps the real case.
  // Opened as a PERMANENT tab (not preview) so dropping several files stacks a new tab
  // each time instead of the preview slot swallowing/replacing the previous one.
  const openExternalFileTab = useCallback(
    (absPath: string, options?: { activate?: boolean }) => {
      const norm = absPath.replace(/\\/g, "/");
      const name = norm.split("/").pop() || norm;
      const path = `ext:${norm.toLowerCase()}`;
      const id = fileTabId(path);
      void openOrFocusTab(id, () =>
        openTab({ id, kind: "file", name, path }, options),
      );
    },
    [openTab],
  );

  useEffect(() => {
    const onOpenExternal = (evt: Event) => {
      const detail = (evt as CustomEvent).detail as { paths?: string[] } | undefined;
      (detail?.paths ?? []).forEach((p, i) => {
        if (typeof p === "string" && p) openExternalFileTab(p, { activate: i === 0 });
      });
    };
    window.addEventListener("ducky:external-files-open", onOpenExternal);
    return () => window.removeEventListener("ducky:external-files-open", onOpenExternal);
  }, [openExternalFileTab]);

  // Cold start: Open-with / ``UEFN-Ducky.exe path`` queued paths before React mounted.
  useEffect(() => {
    const api = getApi();
    if (!api?.consume_pending_open_files) return;
    void api.consume_pending_open_files().then((paths) => {
      (paths ?? []).forEach((p, i) => {
        if (typeof p === "string" && p) openExternalFileTab(p, { activate: i === 0 });
      });
    });
  }, [openExternalFileTab]);

  // Tester dock "Create test": open the new Tester chat + draft without freezing the bridge.
  useEffect(() => {
    const onTesterChat = (evt: Event) => {
      const d = (evt as CustomEvent).detail as
        | { id?: string; name?: string; duckyStyle?: string; draft?: string }
        | undefined;
      if (!d?.id) return;
      if (d.draft) enqueueComposerDraft(d.id, d.draft);
      openChatTab({
        id: d.id,
        name: d.name || "Tester",
        duckyStyle: d.duckyStyle,
      });
      void load();
    };
    window.addEventListener("ducky:tester-open-chat", onTesterChat);
    return () => window.removeEventListener("ducky:tester-open-chat", onTesterChat);
  }, [openChatTab, load]);

  // Agent follow-code opens: land the file in a tab group beside the chat so the
  // chat stays visible while the walkthrough plays. User opens keep openFileTab.
  const openAgentFileTab = useCallback(
    (path: string, name: string, options?: { activate?: boolean }) => {
      const norm = projectRelativePath(path);
      const id = fileTabId(norm);
      const tabName = isVerseFile(norm) ? basename(norm) : name;
      void openOrFocusTab(id, () => {
        if (!getFollowCodeSettings().splitBesideChat) {
          openTab({ id, kind: "file", name: tabName, path: norm }, options);
          return;
        }
        // Anchor on the chat the user is looking at (focused group's active chat),
        // falling back to any chat tab present in the layout.
        const lay = layoutRef.current;
        const tabs = openTabsRef.current;
        const focusedActiveId = lay.groups[lay.focusedGroupId]?.activeTabId;
        const anchorId =
          tabs.find((t) => t.id === focusedActiveId && t.kind === "chat")?.id ??
          tabs.find((t) => t.kind === "chat" && findGroupForTab(lay, t.id))?.id ??
          null;
        openTabBeside({ id, kind: "file", name: tabName, path: norm }, anchorId);
      });
    },
    [openTab, openTabBeside, openTabsRef],
  );

  // Legacy snapshots stored file tab ids with on-disk casing; new ids are lowercase.
  // Remap once at mount or the same file appears as two different tabs.
  useEffect(() => {
    for (const t of openTabsRef.current) {
      if (t.kind === "file" && t.path) {
        const want = fileTabId(t.path);
        if (t.id !== want) remapTabId(t.id, want);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cross-window tab registry: report our tabs; close local copies claimed by another
  // window (drag = move, opens elsewhere never duplicate); activate on focus requests.
  useEffect(() => {
    reportOpenTabs(openTabs.map((t) => t.id));
  }, [openTabs]);

  useEffect(() => {
    return installTabRegistryListener(
      (tabId) => openTabsRef.current.some((t) => t.id === tabId),
      (tabId) => closeTabInLayout(tabId),
      (tabId) => {
        const tab = openTabsRef.current.find((t) => t.id === tabId);
        if (tab) openTab(tab, { activate: true });
      },
    );
  }, [closeTabInLayout, openTab, openTabsRef]);

  // Tab dropped here from ANOTHER window: open it (the registry claim closes the source).
  useEffect(() => {
    return setExternalTabDropHandler((tab) => openTab(tab, { activate: true }));
  }, [openTab]);

  // File renamed: rename the tab in place (VS Code behavior), never orphan it.
  useEffect(() => {
    return installFileRenameListener((oldId, newId, newPath) => {
      if (!openTabsRef.current.some((t) => t.id === oldId)) return;
      remapTabId(oldId, newId);
      setOpenTabs((prev) =>
        prev.map((t) => (t.id === newId ? { ...t, path: newPath, name: basename(newPath) } : t)),
      );
    });
  }, [remapTabId, setOpenTabs, openTabsRef]);

  // File (or folder) deleted: close its tab(s). A deleted file has no editor to show;
  // leaving the tab open also keeps its verse-lsp doc alive, which resurrects the
  // stale diagnostics the Problems panel already purged on this same event.
  useEffect(() => {
    return installFileDeleteListener((deletedId) => {
      const doomed = openTabsRef.current.filter(
        (t) => t.kind === "file" && (t.id === deletedId || t.id.startsWith(`${deletedId}/`)),
      );
      if (doomed.length === 0) return;
      for (const t of doomed) closeTabInLayout(t.id);
      const doomedIds = new Set(doomed.map((t) => t.id));
      // Free ownership immediately so other windows can reopen these ids at once.
      reportOpenTabsNow(openTabsRef.current.filter((t) => !doomedIds.has(t.id)).map((t) => t.id));
    });
  }, [closeTabInLayout, openTabsRef]);

  // Plugin disabled / uninstalled: close every editor tab it owned (browser, discord,
  // ducktactoe, verse-translated, …). Wait for contrib.ready so a cold start cannot
  // wipe restored tabs against an empty enabled_ids list.
  useEffect(() => {
    if (!pluginContrib.ready) return;
    const doomed = orphanedPluginTabs(openTabsRef.current, pluginContrib.enabled_ids);
    if (doomed.length === 0) return;
    for (const t of doomed) {
      if (t.kind === "plugin") {
        clearBrowserPaneBounds(t.id);
        void getApi()?.browser_pane_close?.(t.id);
        clearBoundDucktactoeChat(t.id);
      }
      closeTabInLayout(t.id);
    }
    const doomedIds = new Set(doomed.map((t) => t.id));
    reportOpenTabsNow(openTabsRef.current.filter((t) => !doomedIds.has(t.id)).map((t) => t.id));
    if (doomed.some((t) => isDiscordPluginTabId(t.id) || t.kind === "discord")) {
      refreshDiscordOccupied();
    }
  }, [
    pluginContrib.ready,
    pluginContrib.enabled_ids,
    openTabs,
    closeTabInLayout,
    openTabsRef,
    refreshDiscordOccupied,
  ]);

  const [defaultTerminalShell, setDefaultTerminalShell] = useState<TerminalShell>(DEFAULT_TERMINAL_SHELL);
  const {
    spawnAndOpen,
    parkTerminalTab,
    killTerminalTab,
    reopenTerminalTab,
    parkedTabs,
    restartTerminalTab,
  } = useTerminalTabs(editor);

  const [unsavedPrompt, setUnsavedPrompt] = useState<{
    tabId: string;
    path: string;
    name: string;
    action: "close" | "focus";
  } | null>(null);
  const [unsavedSaving, setUnsavedSaving] = useState(false);

  // Move a tab into a focus window. The focus window opens + claims it; we close
  // our copy immediately (move semantics) instead of waiting for the claim
  // broadcast, whose just-claimed race guard could leave a duplicate behind.
  // Drag tear-off passes screen coords (join/create at drop point). Right-click
  // Focus has no point → solo window, same as sidebar Focus.
  const detachToFocus = useCallback(
    async (tab: EditorTab, at?: { screenX: number; screenY: number }) => {
      if (at) {
        // Declined = the tab was let go back inside this window; leave it alone.
        if (!(await openFocusAtPoint(tab.id, tab.name, at.screenX, at.screenY))) return;
      } else {
        await openFocus(tab.id, tab.name, { solo: true });
      }
      closeTabInLayout(tab.id);
      if (isDiscordPluginTabId(tab.id)) {
        // Keep the dock panel hidden while Discord lives in the focus window.
        setDiscordTabOpen(true);
      }
    },
    [openFocus, openFocusAtPoint, closeTabInLayout],
  );

  const doCloseTab = useCallback(
    async (tabId: string) => {
      const tab = openTabs.find((t) => t.id === tabId);
      if (tab?.kind === "chat" && tab.chatId) dismissCompletionAlert(tab.chatId);
      if (tab?.kind === "settings") {
        if (!(await guardUnsavedChanges())) return;
      }
      if (tab?.kind === "terminal") {
        // Closing the editor tab only hides it — shell keeps running in the header list.
        parkTerminalTab(tab);
        const remaining = openTabs.filter((t) => t.id !== tabId);
        closeTabInLayout(tabId);
        reportOpenTabsNow(remaining.map((t) => t.id));
        return;
      }
      if (tab?.kind === "plugin") {
        // Destroy any native browser pane owned by this plugin tab (no-op otherwise).
        clearBrowserPaneBounds(tabId);
        void getApi()?.browser_pane_close?.(tabId);
        // Next open of Duck-Tac-Toe board creates a fresh branded chat.
        clearBoundDucktactoeChat(tabId);
      }
      closeTabInLayout(tabId);
      // Free ownership immediately so other windows can open this tab at once.
      reportOpenTabsNow(openTabs.filter((t) => t.id !== tabId).map((t) => t.id));
    },
    [openTabs, parkTerminalTab, closeTabInLayout, guardUnsavedChanges],
  );

  const doKillTerminal = useCallback(
    async (tabId: string) => {
      const tab =
        openTabs.find((t) => t.id === tabId) ?? parkedTabs.find((t) => t.id === tabId);
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
      const remaining = openTabs.filter((t) => t.id !== tabId);
      reportOpenTabsNow(remaining.map((t) => t.id));
      await killTerminalTab(tab);
    },
    [openTabs, parkedTabs, killTerminalTab, confirm],
  );

  const { flushBeforeSwitch } = useEditorWorkspace({
    projectSlug,
    allChats,
    foldersLoaded,
    openTabs,
    layout,
    initLayoutState,
  });

  useRegisterEditorWorkspaceFlush(flushBeforeSwitch);

  const handleNewTerminal = useCallback(async () => {
    if (!getTerminalsEnabled()) return;
    const dto = await spawnAndOpen(defaultTerminalShell);
    if (!dto) return;
  }, [defaultTerminalShell, spawnAndOpen]);

  const layoutTabIds = useMemo(() => collectTabIds(layout), [layout]);
  const activePaneTabIds = useMemo(() => collectActiveTabIds(layout), [layout]);

  const activeFilePath = useMemo(() => {
    // FOCUSED group's file first — scanning all groups returned the first group's
    // file, so the sidebar highlight snapped back to it after clicking elsewhere.
    const focused = layout.groups[layout.focusedGroupId];
    if (focused?.activeTabId) {
      const tab = openTabs.find((t) => t.id === focused.activeTabId && t.kind === "file");
      if (tab?.path) return tab.path.replace(/\\/g, "/");
    }
    for (const id of activePaneTabIds) {
      const tab = openTabs.find((t) => t.id === id && t.kind === "file");
      if (tab?.path) return tab.path.replace(/\\/g, "/");
    }
    return undefined;
  }, [activePaneTabIds, openTabs, layout]);

  const activeFileTabId = useMemo(() => {
    const focused = layout.groups[layout.focusedGroupId];
    if (focused?.activeTabId) {
      const tab = openTabs.find((t) => t.id === focused.activeTabId && t.kind === "file");
      if (tab) return tab.id;
    }
    for (const id of activePaneTabIds) {
      const tab = openTabs.find((t) => t.id === id && t.kind === "file");
      if (tab) return tab.id;
    }
    return undefined;
  }, [activePaneTabIds, openTabs, layout]);

  // --- Navigation history (VS Code back/forward). Record the focused pane's active
  // file/chat as a visited location; apply back/forward by opening/focusing that tab. ---
  const nav = useNavigationHistoryOptional();
  const allChatsRef = useRef(allChats);
  allChatsRef.current = allChats;

  const activeNavLoc = useMemo<NavLocation | null>(() => {
    const focused = layout.groups[layout.focusedGroupId];
    const tabId = focused?.activeTabId;
    if (!tabId) return null;
    const tab = openTabs.find((t) => t.id === tabId);
    if (!tab) return null;
    if (tab.kind === "file" && tab.path) {
      return { kind: "file", path: tab.path.replace(/\\/g, "/"), name: tab.name };
    }
    if (tab.kind === "chat" && tab.chatId) {
      return { kind: "chat", chatId: tab.chatId, name: tab.name };
    }
    return null;
  }, [layout, openTabs]);

  useEffect(() => {
    if (activeNavLoc) nav?.record(activeNavLoc);
  }, [activeNavLoc, nav]);

  useEffect(() => {
    if (!nav) return;
    nav.registerOpenApplier((loc) => {
      if (loc.kind === "file") {
        openFileTab(loc.path, loc.name, { activate: true });
      } else if (loc.kind === "chat") {
        const chat = allChatsRef.current.find((c) => c.id === loc.chatId);
        if (chat) openChatTab(chat);
      } else if (loc.kind === "settings") {
        openSettingsTab();
      }
    });
    return () => nav.registerOpenApplier(null);
  }, [nav, openFileTab, openChatTab, openSettingsTab]);

  const { resolveUrl } = useDuckyCatalog();
  const pendingAskPayloadRef = useRef<AskAiPayload | null>(null);
  const [duckyModal, setDuckyModal] = useState<DuckyProfileModalMode | null>(null);

  const openChatWithAskDraft = useCallback(
    (chat: ChatTab, payload: AskAiPayload) => {
      enqueueComposerDraft(chat.id, formatAskDraft(payload));
      openChatBesideFile({
        chat,
        layout,
        fileTabId: activeFileTabId,
        openTab,
        setLayout,
        activateTabInGroup,
        setFocusedGroup,
      });
    },
    [layout, activeFileTabId, openTab, setLayout, activateTabInGroup, setFocusedGroup],
  );

  const handleAskSelection = useCallback(
    (chatId: string, payload: AskAiPayload) => {
      const chat = allChats.find((c) => c.id === chatId);
      if (!chat) return;
      openChatWithAskDraft(chat, payload);
    },
    [allChats, openChatWithAskDraft],
  );

  const handleAskNew = useCallback(
    (payload: AskAiPayload) => {
      pendingAskPayloadRef.current = payload;
      setDuckyModal({ mode: "create", folderId: "", folders, rootChats });
    },
    [folders, rootChats],
  );

  const askAiHandlers = useMemo(
    () => ({ chats: allChats, onAsk: handleAskSelection, onAskNew: handleAskNew }),
    [allChats, handleAskSelection, handleAskNew],
  );
  useRegisterAskAiHandlers(askAiHandlers);

  const problemsDuckyHandlers = useProblemsToDuckyHandlers({
    allChats,
    layout,
    activeFileTabId,
    activeFilePath,
    openTab,
    setLayout,
    activateTabInGroup,
    setFocusedGroup,
    reloadChats: load,
  });
  useRegisterProblemsDuckyHandlers(problemsDuckyHandlers);

  useEffect(() => {
    syncAskAiMenuItems(
      allChats.map((c) => ({
        id: c.id,
        name: c.name,
        iconUrl: resolveUrl(c.duckyStyle),
      })),
    );
  }, [allChats, resolveUrl]);

  const handleRequestSplit = useCallback(() => {
    // Beside-chat placement already puts agent files in their own group —
    // the legacy focused-group split would reshuffle the layout on every open.
    if (getFollowCodeSettings().splitBesideChat) return;
    // Read layout/tabs from refs so this callback stays STABLE across renders.
    // It feeds VerseEditorProvider's `queue` memo; depending on openTabs/layout
    // here rebuilt the EditorActionQueue on every layout change (and, before the
    // useVerseLsp ref fix, drove the verse-lsp bind/unbind loop).
    const lay = layoutRef.current;
    const tabs = openTabsRef.current;
    const idsInLayout = new Set(collectTabIds(lay));
    const fileTab = tabs.find((t) => t.kind === "file" && idsInLayout.has(t.id));
    const chatTab = tabs.find((t) => t.kind === "chat" && idsInLayout.has(t.id));
    if (fileTab && chatTab && fileTab.id !== chatTab.id) {
      splitFocusedGroupWithTab(
        fileTab.id === lay.groups[lay.focusedGroupId]?.activeTabId ? chatTab.id : fileTab.id,
      );
    }
  }, [splitFocusedGroupWithTab]);

  const [filesRefresh, setFilesRefresh] = useState(0);
  const handleFileSync = useCallback(() => setFilesRefresh((n) => n + 1), []);

  const handleFileMoved = useCallback(
    (fromPath: string, toPath: string) => {
      const from = fromPath.replace(/\\/g, "/");
      const to = toPath.replace(/\\/g, "/");
      const remap = (path: string) => {
        if (path === from) return to;
        if (path.startsWith(`${from}/`)) return `${to}/${path.slice(from.length + 1)}`;
        return path;
      };
      setOpenTabs((prev) =>
        prev.map((t) => {
          if (t.kind !== "file" || !t.path) return t;
          const norm = t.path.replace(/\\/g, "/");
          if (norm !== from && !norm.startsWith(`${from}/`)) return t;
          const newPath = remap(norm);
          return {
            ...t,
            id: fileTabId(newPath),
            path: newPath,
            name: isVerseFile(newPath) ? basename(newPath) : newPath.split("/").pop() || t.name,
          };
        }),
      );
      for (const tab of openTabsRef.current) {
        if (tab.kind !== "file" || !tab.path) continue;
        const norm = tab.path.replace(/\\/g, "/");
        if (norm === from || norm.startsWith(`${from}/`)) remapTabId(tab.id, fileTabId(remap(norm)));
      }
      void load();
    },
    [load, openTabsRef, remapTabId, setOpenTabs],
  );

  const sidebarRef = useRef<ChatSidebarHandle>(null);

  const handleChatDeleted = useCallback((chatId: string) => void doCloseTab(chatTabId(chatId)), [doCloseTab]);
  const handleChatRenamed = useCallback(
    (chatId: string, name: string) => {
      setOpenTabs((prev) => prev.map((t) => (t.id === chatTabId(chatId) ? { ...t, name } : t)));
    },
    [setOpenTabs],
  );

  useEffect(() => {
    const chatMap = new Map(allChats.map((c) => [c.id, c]));
    setOpenTabs((prev) => {
      let changed = false;
      const next = prev.map((tab) => {
        if (tab.kind !== "chat" || !tab.chatId) return tab;
        const chat = chatMap.get(tab.chatId);
        if (!chat) return tab;
        const isGroup = Boolean(chat.isGroup);
        if (tab.name === chat.name && tab.duckyStyle === chat.duckyStyle && Boolean(tab.isGroup) === isGroup) {
          return tab;
        }
        changed = true;
        return { ...tab, name: chat.name, duckyStyle: chat.duckyStyle, isGroup };
      });
      return changed ? next : prev;
    });
  }, [allChats, setOpenTabs]);

  const expandFolder = useCallback(
    (folderId: string) => {
      if (!folderId) return;
      const walk = (items: FolderItem[]): FolderItem[] =>
        items.map((f) => ({ ...f, expanded: f.id === folderId ? true : f.expanded, children: walk(f.children) }));
      setFolders((prev) => walk(prev));
    },
    [setFolders],
  );

  const handleRemoteConversation = useCallback(
    (conv: { id: string; title: string; folder_id?: string }) => {
      // Group members / spawn children already live under a parent — opening them
      // steals focus from the live group tab. Sidebar still reloads via load().
      const known =
        allChats.find((c) => c.id === conv.id) || archiveChats.find((c) => c.id === conv.id);
      if (known?.parentConvId) {
        if (conv.folder_id) expandFolder(conv.folder_id);
        return;
      }
      // Duck-Tac-Toe board creates its chat for the in-tab popup — don't steal focus.
      if (shouldSuppressRemoteChatOpen(conv)) {
        if (conv.folder_id) expandFolder(conv.folder_id);
        return;
      }
      openTab({ id: chatTabId(conv.id), kind: "chat", name: conv.title, chatId: conv.id });
      if (conv.folder_id) expandFolder(conv.folder_id);
    },
    [openTab, expandFolder, allChats, archiveChats],
  );

  useChatsChanged(load, handleRemoteConversation);

  const [focusWindowTab, setFocusWindowTab] = useState<{ focusId: string; title: string } | null>(null);

  const openTabRef = useRef(openTab);
  openTabRef.current = openTab;

  useEffect(() => {
    window.__uefnFocusTabReturn = (event) => {
      const tab = focusIdToEditorTab(event.focus_id, event.title);
      if (!tab || tab.kind === "terminal") return;
      // Open locally first; claim after a tick so a still-dying focus window's
      // birth claim race doesn't immediately bounce the tab back and forth.
      openTabRef.current(tab, { activate: true });
      window.setTimeout(() => {
        void getApi()?.claim_tab(tab.id, "main");
      }, 0);
      if (isDiscordPluginTabId(tab.id)) setDiscordTabOpen(true);
    };
    return () => {
      delete window.__uefnFocusTabReturn;
    };
  }, []);

  useEffect(() => {
    window.__uefnFocusTabActive = (event) => {
      setFocusWindowTab({ focusId: event.focus_id, title: event.title });
      const parsed = parseFocusId(event.focus_id);
      if (
        (parsed?.kind === "plugin" && parsed.pluginId === "discord") ||
        isDiscordPluginTabId(event.focus_id)
      ) {
        setDiscordTabOpen(true);
      } else {
        refreshDiscordOccupied();
      }
    };
    return () => {
      delete window.__uefnFocusTabActive;
    };
  }, [refreshDiscordOccupied]);

  const focusWindowActiveTab = useMemo((): EditorTab | undefined => {
    if (!focusWindowTab) return undefined;
    const parsed = parseFocusId(focusWindowTab.focusId);
    if (!parsed) return undefined;
    if (parsed.kind === "chat") {
      return {
        id: chatTabId(parsed.chatId),
        kind: "chat",
        name: focusWindowTab.title || "Chat",
        chatId: parsed.chatId,
      };
    }
    if (parsed.kind === "file") {
      const path = projectRelativePath(parsed.path);
      return {
        id: fileTabId(path),
        kind: "file",
        name: isVerseFile(path) ? basename(path) : focusWindowTab.title || basename(path),
        path,
      };
    }
    return undefined;
  }, [focusWindowTab]);

  // ONE active-tab source: the LOCAL focused group's active tab. Sidebar is-active
  // for chats derives from it. File highlight also follows a visible file tab in a
  // split (tool/agent opened beside chat) and tabs active in a focus window.
  const focusedGroupActiveTab = useMemo(() => {
    const activeId = layout.groups[layout.focusedGroupId]?.activeTabId;
    return activeId ? openTabs.find((t) => t.id === activeId) : undefined;
  }, [layout, openTabs]);

  const sidebarActiveChats = useMemo(() => {
    if (focusedGroupActiveTab?.kind === "chat" && focusedGroupActiveTab.chatId) {
      return [focusedGroupActiveTab.chatId];
    }
    if (focusWindowActiveTab?.kind === "chat" && focusWindowActiveTab.chatId) {
      return [focusWindowActiveTab.chatId];
    }
    return [];
  }, [focusedGroupActiveTab, focusWindowActiveTab]);

  const sidebarActiveFilePath = useMemo(() => {
    if (focusedGroupActiveTab?.kind === "file" && focusedGroupActiveTab.path) {
      return projectRelativePath(focusedGroupActiveTab.path);
    }
    if (focusWindowActiveTab?.kind === "file" && focusWindowActiveTab.path) {
      return projectRelativePath(focusWindowActiveTab.path);
    }
    // Chat is active in main or focus window — don't keep content highlighted from a
    // file tab sitting in another split pane.
    if (
      (focusedGroupActiveTab?.kind === "chat" && focusedGroupActiveTab.chatId) ||
      (focusWindowActiveTab?.kind === "chat" && focusWindowActiveTab.chatId)
    ) {
      return undefined;
    }
    for (const id of activePaneTabIds) {
      const tab = openTabs.find((t) => t.id === id && t.kind === "file");
      if (tab?.path) return projectRelativePath(tab.path);
    }
    return undefined;
  }, [focusedGroupActiveTab, focusWindowActiveTab, activePaneTabIds, openTabs]);

  const syncSidebarPanel = useMemo((): "chats" | "files" => {
    if (focusedGroupActiveTab?.kind === "file" || focusWindowActiveTab?.kind === "file") {
      return "files";
    }
    return "chats";
  }, [focusedGroupActiveTab, focusWindowActiveTab]);

  // "Open in focus window" from the sidebar: move semantics — the focus window
  // claims the tab; close any local copy right away.
  const handleFocusChat = useCallback(
    async (chat: { id: string; name: string }) => {
      const tabId = chatTabId(chat.id);
      await openFocus(tabId, chat.name, { solo: true });
      if (openTabsRef.current.some((t) => t.id === tabId)) closeTabInLayout(tabId);
    },
    [openFocus, closeTabInLayout, openTabsRef],
  );

  const handleFocusFile = useCallback(
    async (path: string, name: string) => {
      const tabId = fileTabId(path.replace(/\\/g, "/"));
      await openFocus(tabId, name, { solo: true });
      if (openTabsRef.current.some((t) => t.id === tabId)) closeTabInLayout(tabId);
    },
    [openFocus, closeTabInLayout, openTabsRef],
  );

  // Sidebar row dragged out of the window: tear off into a focus window at the
  // drop point (same move semantics as handleFocusChat/handleFocusFile).
  const handleDetachChatAt = useCallback(
    async (chat: { id: string; name: string }, at: { screenX: number; screenY: number }) => {
      const tabId = chatTabId(chat.id);
      await openFocusAtPoint(tabId, chat.name, at.screenX, at.screenY);
      if (openTabsRef.current.some((t) => t.id === tabId)) closeTabInLayout(tabId);
    },
    [openFocusAtPoint, closeTabInLayout, openTabsRef],
  );

  const handleDetachFileAt = useCallback(
    async (path: string, name: string, at: { screenX: number; screenY: number }) => {
      const tabId = fileTabId(path.replace(/\\/g, "/"));
      await openFocusAtPoint(tabId, name, at.screenX, at.screenY);
      if (openTabsRef.current.some((t) => t.id === tabId)) closeTabInLayout(tabId);
    },
    [openFocusAtPoint, closeTabInLayout, openTabsRef],
  );

  // Sidebar selects route through the registry (openChatTab/openFileTab →
  // openOrFocusTab): live owner raised wherever it is, else open HERE in main.
  // Single sidebar click opens a reusable preview (italic) tab; double-click pins it.
  const handleSidebarChatSelect = useCallback(
    (chat: ChatTab) => {
      openChatTab(chat, { preview: true });
    },
    [openChatTab],
  );

  const handleSidebarChatOpenPermanent = useCallback(
    (chat: ChatTab) => {
      openChatTab(chat, { preview: false });
    },
    [openChatTab],
  );

  const handleSidebarFileSelect = useCallback(
    (path: string, name: string) => {
      openSidebarProjectFile(path, name, (p, n) => openFileTab(p, n, { preview: true }));
    },
    [openFileTab],
  );

  const handleSidebarFileOpenPermanent = useCallback(
    (path: string, name: string) => {
      openSidebarProjectFile(path, name, (p, n) => openFileTab(p, n, { preview: false }));
    },
    [openFileTab],
  );

  /** Drag a ducky from the left/right dock onto the editor — honor split zones. */
  const handleOpenChatInEditor = useCallback(
    (chat: { id: string; name: string }, placement?: { groupId: string; zone: EditorDropZone }) => {
      dismissCompletionAlert(chat.id);
      const id = chatTabId(chat.id);
      if (!placement) {
        openChatTab(chat, { preview: false });
        return;
      }
      openTabInZone(
        {
          id,
          kind: "chat",
          name: chat.name,
          chatId: chat.id,
        },
        placement.groupId,
        placement.zone,
      );
      emitAppHook("agent.selected", { chatId: chat.id });
    },
    [openChatTab, openTabInZone],
  );

  /** Drag a file from Content onto the editor — honor split zones. */
  const handleOpenFileInEditor = useCallback(
    (path: string, name: string, placement?: { groupId: string; zone: EditorDropZone }) => {
      openSidebarProjectFile(path, name, (p, n) => {
        if (!placement) {
          openFileTab(p, n, { preview: false });
          return;
        }
        const norm = projectRelativePath(p);
        const id = fileTabId(norm);
        const tabName = isVerseFile(norm) ? basename(norm) : n;
        openTabInZone(
          { id, kind: "file", name: tabName, path: norm },
          placement.groupId,
          placement.zone,
        );
      });
    },
    [openFileTab, openTabInZone],
  );

  useRegisterQuickOpenHandlers(
    useMemo<QuickOpenHandlers | null>(
      () =>
        projectSlug
          ? { openTabs, folders, rootChats, onOpenFile: handleSidebarFileSelect, onOpenChat: handleSidebarChatSelect }
          : null,
      [projectSlug, openTabs, folders, rootChats, handleSidebarFileSelect, handleSidebarChatSelect],
    ),
  );

  const hiddenChatTabs = useMemo(
    () =>
      openTabs
        .filter((t) => {
          if (t.kind !== "chat" || !t.chatId) return false;
          const gid = findGroupForTab(layout, t.id);
          return gid ? layout.groups[gid]?.activeTabId !== t.id : false;
        })
        .map((t) => allChats.find((c) => c.id === t.chatId) ?? { id: t.chatId!, name: t.name, duckyStyle: t.duckyStyle }),
    [openTabs, layout, allChats],
  );

  // No detached-but-still-open tabs anymore: a tab moved to a focus window is
  // CLOSED here (registry move semantics), so main never keeps hidden file editors.
  const hiddenFileTabs = useMemo<EditorTab[]>(() => [], []);

  const hiddenTerminalTabs = useMemo(
    () =>
      openTabs.filter((t) => {
        if (t.kind !== "terminal" || !t.terminalSessionId || !t.terminalWsUrl) return false;
        const gid = findGroupForTab(layout, t.id);
        return gid ? layout.groups[gid]?.activeTabId !== t.id : false;
      }),
    [openTabs, layout],
  );

  const handleRestartTerminal = useCallback(
    (tab: EditorTab) => {
      void restartTerminalTab(tab);
    },
    [restartTerminalTab],
  );

  useChatCompletionAlerts({ runningChatIds });
  const completionAlertChatIds = useCompletionAlertChatIds();
  useSpokenReplies();

  const handleCreateChat = useCallback(
    () => {
      pendingAskPayloadRef.current = null;
      setDuckyModal({ mode: "create", folderId: "", folders, rootChats });
    },
    [folders, rootChats],
  );

  const handleRequestCreateDucky = useCallback(
    (ctx: { folderId: string }) => {
      pendingAskPayloadRef.current = null;
      setDuckyModal({ mode: "create", folderId: ctx.folderId, folders, rootChats });
    },
    [folders, rootChats],
  );

  const handleCloseDuckyModal = useCallback(() => {
    pendingAskPayloadRef.current = null;
    setDuckyModal(null);
  }, []);

  const handleDuckyCreated = useCallback(
    (chat: ChatTab) => {
      const pending = pendingAskPayloadRef.current;
      pendingAskPayloadRef.current = null;
      setPendingChats((prev) => [...prev.filter((c) => c.id !== chat.id), chat]);
      if (pending) {
        openChatWithAskDraft(chat, pending);
      } else {
        openChatTab(chat);
      }
      void load().finally(() => {
        setPendingChats((prev) => prev.filter((c) => c.id !== chat.id));
      });
    },
    [load, openChatTab, openChatWithAskDraft],
  );

  const handleDeleteDuckyFromEditor = useCallback(
    (chat: DuckyEditTarget) => {
      void (async () => {
        const archived = archiveChats.some((c) => c.id === chat.id);
        if (
          archived &&
          !(await confirm({
            message: `Permanently delete "${chat.name}"? This cannot be undone.`,
            confirmLabel: "Delete permanently",
            danger: true,
          }))
        )
          return;
        setDuckyModal(null);
        const api = getApi();
        if (!api) return;
        if (archived) {
          await api.delete_conversation(chat.id);
        } else {
          await api.move_conversation(chat.id, ARCHIVE_FOLDER_ID);
        }
        handleChatDeleted(chat.id);
        await load();
      })();
    },
    [archiveChats, confirm, handleChatDeleted, load],
  );

  const hasVisibleTabs = layoutTabIds.length > 0;

  return (
    <VerseEditorProvider
      onOpenFile={openFileTab}
      onAgentOpenFile={openAgentFileTab}
      onRequestSplit={handleRequestSplit}
      onFileSync={handleFileSync}
      projectPath={projectPath}
    >
      <ChatViewEditorChrome
        layoutMode={layoutMode}
        projectSlug={projectSlug}
        projectPath={projectPath}
        openTabs={openTabs}
        layout={layout}
        setLayout={setLayout}
        openChatTab={openChatTab}
        openFileTab={openFileTab}
        openPlanTab={openPlanTab}
        promoteTab={promoteTab}
        doCloseTab={doCloseTab}
        doKillTerminal={doKillTerminal}
        reopenTerminalTab={reopenTerminalTab}
        parkedTabs={parkedTabs}
        detachToFocus={detachToFocus}
        activateTabInGroup={activateTabInGroup}
        setFocusedGroup={setFocusedGroup}
        reorderTabsInGroup={reorderTabsInGroup}
        dropTabOnGroup={dropTabOnGroup}
        toggleGroupLockInLayout={toggleGroupLockInLayout}
        unsavedPrompt={unsavedPrompt}
        setUnsavedPrompt={setUnsavedPrompt}
        unsavedSaving={unsavedSaving}
        setUnsavedSaving={setUnsavedSaving}
        folders={folders}
        setFolders={setFolders}
        rootChats={rootChats}
        setRootChats={setRootChats}
        archiveChats={archiveChats}
        setArchiveChats={setArchiveChats}
        load={load}
        sidebarRef={sidebarRef}
        sidebarActiveChats={sidebarActiveChats}
        sidebarActiveFilePath={sidebarActiveFilePath}
        syncSidebarPanel={syncSidebarPanel}
        runningChatIds={runningChatIds}
        completionAlertChatIds={completionAlertChatIds}
        allChats={allChats}
        hiddenChatTabs={hiddenChatTabs}
        hiddenFileTabs={hiddenFileTabs}
        hiddenTerminalTabs={hiddenTerminalTabs}
        onRestartTerminal={handleRestartTerminal}
        activeFilePath={activeFilePath}
        hasVisibleTabs={hasVisibleTabs}
        handleSidebarChatSelect={handleSidebarChatSelect}
        handleSidebarChatOpenPermanent={handleSidebarChatOpenPermanent}
        handleFocusChat={handleFocusChat}
        handleSidebarFileSelect={handleSidebarFileSelect}
        handleSidebarFileOpenPermanent={handleSidebarFileOpenPermanent}
        handleFocusFile={handleFocusFile}
        handleDetachChatAt={handleDetachChatAt}
        handleDetachFileAt={handleDetachFileAt}
        handleOpenChatInEditor={handleOpenChatInEditor}
        handleOpenFileInEditor={handleOpenFileInEditor}
        handleFileMoved={handleFileMoved}
        handleChatDeleted={handleChatDeleted}
        handleChatRenamed={handleChatRenamed}
        handleRequestCreateDucky={handleRequestCreateDucky}
        handleDuckyCreated={handleDuckyCreated}
        handleCloseDuckyModal={handleCloseDuckyModal}
        duckyModal={duckyModal}
        handleDeleteDuckyFromEditor={handleDeleteDuckyFromEditor}
        handleCreateChat={handleCreateChat}
        filesRefresh={filesRefresh}
        defaultTerminalShell={defaultTerminalShell}
        setDefaultTerminalShell={setDefaultTerminalShell}
        handleNewTerminal={handleNewTerminal}
      />
    </VerseEditorProvider>
  );
}

interface ChatViewEditorChromeProps {
  layoutMode: ChatLayoutMode;
  projectSlug: string;
  projectPath: string;
  openTabs: EditorTab[];
  layout: ReturnType<typeof useEditorLayout>["layout"];
  setLayout: ReturnType<typeof useEditorLayout>["setLayout"];
  openChatTab: (chat: ChatTab, options?: { preview?: boolean }) => void;
  openFileTab: (path: string, name: string, options?: { activate?: boolean; preview?: boolean }) => void;
  openPlanTab: (chatId: string, title?: string, projectRoot?: string) => void;
  promoteTab: (tabId: string) => void;
  doCloseTab: (tabId: string) => void | Promise<void>;
  doKillTerminal: (tabId: string) => void;
  reopenTerminalTab: (tabId: string) => boolean;
  parkedTabs: EditorTab[];
  detachToFocus: (tab: EditorTab, at?: { screenX: number; screenY: number }) => Promise<void>;
  activateTabInGroup: ReturnType<typeof useEditorLayout>["activateTabInGroup"];
  setFocusedGroup: ReturnType<typeof useEditorLayout>["setFocusedGroup"];
  reorderTabsInGroup: ReturnType<typeof useEditorLayout>["reorderTabsInGroup"];
  dropTabOnGroup: ReturnType<typeof useEditorLayout>["dropTabOnGroup"];
  toggleGroupLockInLayout: ReturnType<typeof useEditorLayout>["toggleGroupLockInLayout"];
  unsavedPrompt: { tabId: string; path: string; name: string; action: "close" | "focus" } | null;
  setUnsavedPrompt: React.Dispatch<
    React.SetStateAction<{ tabId: string; path: string; name: string; action: "close" | "focus" } | null>
  >;
  unsavedSaving: boolean;
  setUnsavedSaving: React.Dispatch<React.SetStateAction<boolean>>;
  folders: FolderItem[];
  setFolders: React.Dispatch<React.SetStateAction<FolderItem[]>>;
  rootChats: ChatTab[];
  setRootChats: React.Dispatch<React.SetStateAction<ChatTab[]>>;
  archiveChats: FolderItem["chats"];
  setArchiveChats: React.Dispatch<React.SetStateAction<FolderItem["chats"]>>;
  load: () => Promise<void>;
  sidebarRef: React.RefObject<ChatSidebarHandle | null>;
  sidebarActiveChats: string[];
  sidebarActiveFilePath: string | undefined;
  syncSidebarPanel: "chats" | "files";
  runningChatIds: Set<string>;
  completionAlertChatIds: ReadonlySet<string>;
  allChats: ChatTab[];
  hiddenChatTabs: ChatTab[];
  hiddenFileTabs: EditorTab[];
  hiddenTerminalTabs: EditorTab[];
  onRestartTerminal: (tab: EditorTab) => void;
  activeFilePath: string | undefined;
  hasVisibleTabs: boolean;
  handleSidebarChatSelect: (chat: ChatTab) => void;
  handleSidebarChatOpenPermanent: (chat: ChatTab) => void;
  handleFocusChat: (chat: { id: string; name: string }) => Promise<void>;
  handleSidebarFileSelect: (path: string, name: string) => void;
  handleSidebarFileOpenPermanent: (path: string, name: string) => void;
  handleFocusFile: (path: string, name: string) => Promise<void>;
  handleDetachChatAt: (chat: { id: string; name: string }, at: { screenX: number; screenY: number }) => Promise<void>;
  handleDetachFileAt: (path: string, name: string, at: { screenX: number; screenY: number }) => Promise<void>;
  handleOpenChatInEditor: (
    chat: { id: string; name: string },
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  handleOpenFileInEditor: (
    path: string,
    name: string,
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  handleFileMoved: (from: string, to: string) => void;
  handleChatDeleted: (chatId: string) => void;
  handleChatRenamed: (chatId: string, name: string) => void;
  handleRequestCreateDucky: (ctx: { folderId: string }) => void;
  handleDuckyCreated: (chat: ChatTab) => void;
  handleCloseDuckyModal: () => void;
  duckyModal: DuckyProfileModalMode | null;
  handleDeleteDuckyFromEditor: (chat: DuckyEditTarget) => void;
  handleCreateChat: () => void;
  filesRefresh: number;
  defaultTerminalShell: TerminalShell;
  setDefaultTerminalShell: React.Dispatch<React.SetStateAction<TerminalShell>>;
  handleNewTerminal: () => Promise<void>;
}

function ChatViewEditorChrome({
  layoutMode,
  projectSlug,
  openTabs,
  layout,
  setLayout,
  openChatTab,
  openFileTab,
  openPlanTab,
  promoteTab,
  doCloseTab,
  doKillTerminal,
  reopenTerminalTab,
  parkedTabs,
  detachToFocus,
  activateTabInGroup,
  setFocusedGroup,
  reorderTabsInGroup,
  dropTabOnGroup,
  toggleGroupLockInLayout,
  unsavedPrompt,
  setUnsavedPrompt,
  unsavedSaving,
  setUnsavedSaving,
  folders,
  setFolders,
  rootChats,
  setRootChats,
  archiveChats,
  setArchiveChats,
  load,
  sidebarRef,
  sidebarActiveChats,
  sidebarActiveFilePath,
  syncSidebarPanel,
  runningChatIds,
  completionAlertChatIds,
  allChats,
  hiddenChatTabs,
  hiddenFileTabs,
  hiddenTerminalTabs,
  onRestartTerminal,
  activeFilePath,
  hasVisibleTabs,
  handleSidebarChatSelect,
  handleSidebarChatOpenPermanent,
  handleFocusChat,
  handleSidebarFileSelect,
  handleSidebarFileOpenPermanent,
  handleFocusFile,
  handleDetachChatAt,
  handleDetachFileAt,
  handleOpenChatInEditor,
  handleOpenFileInEditor,
  handleFileMoved,
  handleChatDeleted,
  handleChatRenamed,
  handleRequestCreateDucky,
  handleDuckyCreated,
  handleCloseDuckyModal,
  duckyModal,
  handleDeleteDuckyFromEditor,
  handleCreateChat,
  filesRefresh,
  defaultTerminalShell,
  setDefaultTerminalShell,
  handleNewTerminal,
  projectPath,
}: ChatViewEditorChromeProps) {
  const verseEditor = useVerseEditorOptional();

  // "Working in" a preview file (editing its content) pins it — matches VS Code, where
  // a preview tab goes from italic to normal the moment you make an edit. dirtyPaths
  // keys are normPath (lowercased) — compare with isPathDirty, not a raw path.
  const dirtyPaths = verseEditor?.dirtyPaths;
  const isPathDirty = verseEditor?.isPathDirty;
  useEffect(() => {
    if (!dirtyPaths || !isPathDirty) return;
    for (const tab of openTabs) {
      if (tab.kind === "file" && tab.preview && tab.path && isPathDirty(tab.path)) {
        promoteTab(tab.id);
      }
    }
  }, [dirtyPaths, isPathDirty, openTabs, promoteTab]);

  const requestCloseTab = useCallback(
    (tabId: string) => {
      const tab = openTabs.find((t) => t.id === tabId);
      if (tab?.kind === "file" && tab.path && verseEditor?.isPathDirty(tab.path.replace(/\\/g, "/"))) {
        setUnsavedPrompt({ tabId, path: tab.path.replace(/\\/g, "/"), name: tab.name, action: "close" });
        return;
      }
      void doCloseTab(tabId);
    },
    [openTabs, verseEditor, doCloseTab, setUnsavedPrompt],
  );

  const requestFocusTab = useCallback(
    (tab: EditorTab, at?: { screenX: number; screenY: number }) => {
      if (tab.kind === "file" && tab.path && verseEditor?.isPathDirty(tab.path.replace(/\\/g, "/"))) {
        setUnsavedPrompt({ tabId: tab.id, path: tab.path.replace(/\\/g, "/"), name: tab.name, action: "focus" });
        return;
      }
      void detachToFocus(tab, at);
    },
    [verseEditor, detachToFocus, setUnsavedPrompt],
  );

  const handleSaveTab = useCallback(
    (tab: EditorTab) => {
      if (tab.kind === "file" && tab.path && verseEditor) void verseEditor.savePath(tab.path.replace(/\\/g, "/"));
    },
    [verseEditor],
  );

  const revealEditorPosition = useCallback(
    (path: string, line: number, column: number) => {
      if (verseEditor?.revealIfOpen(path, line, column)) return;
      verseEditor?.requestReveal(path, line, column);
    },
    [verseEditor],
  );

  const handleOpenFileAt = useCallback(
    (path: string, name: string, options?: { line?: number }) => {
      openProjectFileAt(path, name, openFileTab, revealEditorPosition, options);
    },
    [openFileTab, revealEditorPosition],
  );

  const handleTabActivated = useCallback(
    (tab: EditorTab) => {
      focusActivatedEditorTab(tab, revealEditorPosition);
    },
    [revealEditorPosition],
  );

  const handleRevealFileInSidebar = useCallback((path: string) => {
    sidebarRef.current?.revealFileInSidebar(path);
  }, [sidebarRef]);

  const handleRevealFileInExplorer = useCallback((path: string) => {
    sidebarRef.current?.revealFileInExplorer(path);
  }, [sidebarRef]);

  const saveAndClose = useCallback(async () => {
    if (!unsavedPrompt || !verseEditor) return;
    setUnsavedSaving(true);
    const ok = await verseEditor.savePath(unsavedPrompt.path);
    setUnsavedSaving(false);
    if (!ok) return;
    const { tabId, action } = unsavedPrompt;
    setUnsavedPrompt(null);
    if (action === "focus") {
      const tab = openTabs.find((t) => t.id === tabId);
      if (tab) void detachToFocus(tab);
    } else {
      void doCloseTab(tabId);
    }
  }, [unsavedPrompt, verseEditor, openTabs, detachToFocus, doCloseTab, setUnsavedPrompt, setUnsavedSaving]);

  const mainTargetRef = useUiTarget("shell.main", { kind: "button", label: "Main area", route: "chat" });

  return (
    <>
      <ChatHeaderBridge activeFilePath={activeFilePath} projectPath={projectPath} openTabs={openTabs} />
      <TerminalHeaderBridge
        projectPath={projectPath}
        openTabs={openTabs}
        parkedTabs={parkedTabs}
        layout={layout}
        defaultTerminalShell={defaultTerminalShell}
        setDefaultTerminalShell={setDefaultTerminalShell}
        onNewTerminal={() => void handleNewTerminal()}
        onCloseTerminal={(tabId) => void doKillTerminal(tabId)}
        onGotoTerminal={(tabId) => {
          if (reopenTerminalTab(tabId)) return;
          const groupId = findGroupForTab(layout, tabId);
          if (!groupId) return;
          setFocusedGroup(groupId);
          activateTabInGroup(groupId, tabId);
        }}
        activateTabInGroup={activateTabInGroup}
        setFocusedGroup={setFocusedGroup}
      />
      <VerseWorkflowBridge enabled={!!projectPath.trim()} />
      <WorkspaceDockProvider>
        <div className={`no-drag chat-view-layout chat-view-layout--${layoutMode}`}>
          <WorkspaceDockLayout
            ref={sidebarRef as React.RefObject<ChatSidebarHandle>}
            layoutMode={layoutMode}
            activeFilePath={activeFilePath}
            historyRefreshKey={verseEditor?.historyRefreshKey ?? 0}
            sidebarProps={{
              projectSlug,
              activeChats: sidebarActiveChats,
              activeFilePath: sidebarActiveFilePath,
              syncSidebarPanel,
              runningChatIds,
              completionAlertChatIds,
              folders,
              setFolders,
              rootChats,
              setRootChats,
              archiveChats,
              setArchiveChats,
              load,
              onChatSelect: handleSidebarChatSelect,
              onChatOpenPermanent: handleSidebarChatOpenPermanent,
              onFocusChat: handleFocusChat,
              onFileSelect: handleSidebarFileSelect,
              onFileOpenPermanent: handleSidebarFileOpenPermanent,
              onFocusFile: handleFocusFile,
              onDetachChatAt: handleDetachChatAt,
              onDetachFileAt: handleDetachFileAt,
              onOpenChatInEditor: handleOpenChatInEditor,
              onOpenFileInEditor: handleOpenFileInEditor,
              onFileMoved: handleFileMoved,
              onChatDeleted: handleChatDeleted,
              onChatRenamed: handleChatRenamed,
              onEditDucky: (chat: ChatTab) => requestOpenDuckyEditor(chat),
              onRequestCreateDucky: handleRequestCreateDucky,
              filesRefresh,
            }}
          >
            <DuckyProfileModal
              open={duckyModal !== null}
              state={duckyModal}
              onClose={handleCloseDuckyModal}
              onCreated={handleDuckyCreated}
              onSaved={load}
              onDelete={handleDeleteDuckyFromEditor}
              deleteActionLabel={
                duckyModal?.mode === "edit" && archiveChats.some((c) => c.id === duckyModal.chat.id)
                  ? "Delete permanently"
                  : "Archive"
              }
            />
            <section ref={mainTargetRef} className="chat-view-main-section">
              <div className="chat-view-editor-area">
                {!hasVisibleTabs ? (
                  <EditorTabDropSurface
                    targetGroupId={layout.focusedGroupId}
                    onDropTab={dropTabOnGroup}
                    className="editor-tab-drop-surface--fill"
                  >
                    <ChatEmptyState folders={folders} rootChats={rootChats} onChatSelect={openChatTab} onCreateChat={handleCreateChat} />
                  </EditorTabDropSurface>
                ) : (
                  <SplitEditorLayout
                    layout={layout}
                    openTabs={openTabs}
                    onLayoutChange={setLayout}
                    hiddenChatTabs={hiddenChatTabs}
                    hiddenFileTabs={hiddenFileTabs}
                    hiddenTerminalTabs={hiddenTerminalTabs}
                    onRestartTerminal={onRestartTerminal}
                    allChats={allChats}
                    folders={folders}
                    contextFilePath={activeFilePath}
                    runningChatIds={runningChatIds}
                    onOpenChat={openChatTab}
                    onOpenFile={handleOpenFileAt}
                    onOpenPlan={openPlanTab}
                    onDismissChatAlert={(id) => dismissCompletionAlert(id)}
                    onCloseTab={requestCloseTab}
                    onReorderTabs={reorderTabsInGroup}
                    onDropTab={dropTabOnGroup}
                    onFocusGroup={setFocusedGroup}
                    onActivateTab={activateTabInGroup}
                    onFocusTab={requestFocusTab}
                    onSaveTab={handleSaveTab}
                    onTabActivated={handleTabActivated}
                    onPromoteTab={promoteTab}
                    onRevealFileInSidebar={handleRevealFileInSidebar}
                    onRevealFileInExplorer={handleRevealFileInExplorer}
                    completionAlertChatIds={completionAlertChatIds}
                    onToggleGroupLock={toggleGroupLockInLayout}
                  />
                )}
              </div>
            </section>
          </WorkspaceDockLayout>
        </div>
      </WorkspaceDockProvider>
      {unsavedPrompt ? (
        <UnsavedChangesModal
          fileName={unsavedPrompt.name}
          saving={unsavedSaving}
          onSave={() => void saveAndClose()}
          onDiscard={() => {
            const { tabId, action } = unsavedPrompt;
            setUnsavedPrompt(null);
            if (action === "focus") {
              const tab = openTabs.find((t) => t.id === tabId);
              if (tab) void detachToFocus(tab);
            } else void doCloseTab(tabId);
          }}
          onCancel={() => setUnsavedPrompt(null)}
        />
      ) : null}
    </>
  );
}
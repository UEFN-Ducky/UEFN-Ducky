import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppearanceProvider } from "../theme/AppearanceContext";
import { DuckyCatalogProvider } from "../components/ducky/DuckyCatalogContext";
import { ThemeProvider } from "../theme/ThemeProvider";
import { FocusShell } from "../components/FocusShell";
import { FocusHeaderBar, useFocusCloseWindow } from "../components/FocusHeaderBar";
import { ChatHeaderBridge } from "../components/ChatHeaderBridge";
import { VerseWorkflowBridge } from "../components/VerseWorkflowBridge";
import { useListenerStatus } from "../hooks/useListenerStatus";
import { useConnectionIcon } from "../hooks/useConnectionIcon";
import { SplitEditorLayout } from "../components/SplitEditorLayout";
import { EditorTabDropSurface } from "../components/EditorTabDropSurface";
import { PanelApiGate } from "../components/PanelApiGate";
import { useAgentEventBus } from "../hooks/useAgentEventBus";
import { useRunningAgents } from "../hooks/useRunningAgents";
import { useFocusWindowTabs } from "../hooks/useFocusWindowTabs";
import { dismissCompletionAlert, useCompletionAlertChatIds } from "../hooks/useChatCompletionAlerts";
import { onApiReady } from "../hooks/onApiReady";
import { VerseEditorProvider, useVerseEditorOptional } from "../verse-editor";
import { AppHeaderActionsProvider } from "../contexts/AppHeaderActionsContext";
import { QuickOpenBridgeProvider, useRegisterQuickOpenHandlers, type QuickOpenHandlers } from "../contexts/QuickOpenBridge";
import { AskAiBridgeProvider, useRegisterAskAiHandlers, type AskAiPayload } from "../contexts/AskAiBridge";
import { ProblemsDuckyBridgeProvider, useRegisterProblemsDuckyHandlers } from "../contexts/ProblemsDuckyBridge";
import { useProblemsToDuckyHandlers } from "../hooks/useProblemsToDuckyHandlers";
import { TerminalCommandApprovalProvider } from "../terminal/TerminalCommandApproval";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { TerminalsSettingsProvider } from "../contexts/TerminalsSettingsContext";
import { useChatFolders } from "../hooks/useChatFolders";
import type { ChatTab, EditorDropZone, EditorTab } from "../types/panel";
import { fileTabId } from "../types/panel";
import { focusIdToEditorTab } from "../hooks/useFocusWindow";
import { getApi } from "../hooks/usePanelApi";
import { openSidebarProjectFile } from "../utils/openSidebarProjectFile";
import { focusActivatedEditorTab, openProjectFileAt } from "../utils/openProjectFileAt";
import { collectActiveTabIds, collectTabIds, findGroupForTab } from "../utils/editorLayoutOps";
import { formatAskDraft } from "../utils/formatAskDraft";
import { openChatBesideFile } from "../utils/openChatBesideFile";
import { enqueueComposerDraft } from "../hooks/chatComposerCache";
import { syncAskAiMenuItems } from "../verse-editor/monaco/registerAskAiContextMenu";
import { UnsavedChangesModal } from "../components/UnsavedChangesModal";
import { TerminalHeaderBridge } from "../components/TerminalHeaderBridge";
import { DEFAULT_TERMINAL_SHELL, type TerminalShell } from "../terminal/types";
import { getTerminalsEnabled } from "../contexts/TerminalsSettingsContext";
import { useDuckyCatalog } from "../components/ducky/DuckyCatalogContext";
import { DuckyProfileModal, type DuckyProfileModalMode } from "../components/ducky/DuckyProfileModal";

interface FocusViewProps {
  focusId: string;
}

function flattenChats(folders: import("../types/panel").FolderItem[], rootChats: import("../types/panel").FolderItem["chats"] = []): ChatTab[] {
  const out: ChatTab[] = [...rootChats];
  const walk = (items: import("../types/panel").FolderItem[]) => {
    for (const folder of items) {
      out.push(...folder.chats);
      walk(folder.children);
    }
  };
  walk(folders);
  return out;
}

function FocusViewContent({ focusId }: FocusViewProps) {
  useAgentEventBus();
  const titleParam = new URLSearchParams(window.location.search).get("title") ?? "";

  const {
    openTabs,
    layout,
    setLayout,
    closeTab,
    killTerminal,
    reopenTerminalTab,
    parkedTabs,
    openFileTab,
    openChatTab,
    openTab,
    activateTabInGroup,
    setFocusedGroup,
    reorderTabsInGroup,
    dropTabOnGroup,
    splitFocusedGroupWithTab,
    toggleGroupLockInLayout,
    notifyMainFocusTabActive,
    restartTerminalTab,
    spawnAndOpen,
  } = useFocusWindowTabs(focusId, titleParam);

  const [defaultTerminalShell, setDefaultTerminalShell] = useState<TerminalShell>(DEFAULT_TERMINAL_SHELL);

  const [projectPath, setProjectPath] = useState("");
  const [project, setProject] = useState({ name: "", path: "", slug: "" });
  const listener = useListenerStatus(8000);
  useConnectionIcon(listener);
  const hasProject = !!projectPath.trim();
  const runningChatIds = useRunningAgents();
  const { folders, rootChats, load } = useChatFolders(0);
  const handleCloseWindow = useFocusCloseWindow();

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_project_info().then((info) => {
        setProjectPath(info.path || "");
        setProject({ name: info.name || "", path: info.path || "", slug: info.slug || "" });
      });
    });
  }, []);

  // Explorer → editor drops in a focus window: open as ext: tabs in this window.
  useEffect(() => {
    const onOpenExternal = (evt: Event) => {
      const detail = (evt as CustomEvent).detail as { paths?: string[] } | undefined;
      (detail?.paths ?? []).forEach((p, i) => {
        if (typeof p !== "string" || !p) return;
        const norm = p.replace(/\\/g, "/");
        const name = norm.split("/").pop() || norm;
        const path = `ext:${norm.toLowerCase()}`;
        openTab(
          { id: fileTabId(path), kind: "file", name, path },
          { preview: true, activate: i === 0 },
        );
      });
    };
    window.addEventListener("ducky:external-files-open", onOpenExternal);
    return () => window.removeEventListener("ducky:external-files-open", onOpenExternal);
  }, [openTab]);

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

  const allChats = useMemo(() => flattenChats(folders, rootChats), [folders, rootChats]);

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

  const handleCloseDuckyModal = useCallback(() => {
    pendingAskPayloadRef.current = null;
    setDuckyModal(null);
  }, []);

  const handleDuckyCreated = useCallback(
    (chat: ChatTab) => {
      const pending = pendingAskPayloadRef.current;
      pendingAskPayloadRef.current = null;
      if (pending) {
        openChatWithAskDraft(chat, pending);
      } else {
        openChatTab(chat);
      }
      void load();
    },
    [load, openChatTab, openChatWithAskDraft],
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

  const hiddenChatTabs = useMemo(
    () =>
      openTabs
        .filter((t) => {
          if (t.kind !== "chat" || !t.chatId) return false;
          const gid = findGroupForTab(layout, t.id);
          return gid ? layout.groups[gid]?.activeTabId !== t.id : false;
        })
        .map((t) => ({ id: t.chatId!, name: t.name, duckyStyle: t.duckyStyle })),
    [openTabs, layout],
  );

  const hiddenFileTabs = useMemo(
    () =>
      openTabs.filter((t) => {
        if (t.kind !== "file" || !t.path) return false;
        const gid = findGroupForTab(layout, t.id);
        return gid ? layout.groups[gid]?.activeTabId !== t.id : true;
      }),
    [openTabs, layout],
  );

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

  const handleNewTerminal = useCallback(async () => {
    if (!getTerminalsEnabled()) return;
    await spawnAndOpen(defaultTerminalShell);
  }, [defaultTerminalShell, spawnAndOpen]);

  const handleOpenChat = useCallback((chat: ChatTab) => openChatTab(chat), [openChatTab]);
  const handleOpenFile = useCallback(
    (path: string, name: string) => openSidebarProjectFile(path, name, openFileTab),
    [openFileTab],
  );

  const quickOpenHandlers = useMemo<QuickOpenHandlers | null>(() => {
    if (!hasProject) return null;
    return { openTabs, folders, rootChats, onOpenFile: handleOpenFile, onOpenChat: handleOpenChat };
  }, [hasProject, openTabs, folders, rootChats, handleOpenFile, handleOpenChat]);

  useRegisterQuickOpenHandlers(quickOpenHandlers);

  const handleRequestSplit = useCallback(() => {
    const layoutTabIds = collectTabIds(layout);
    const fileTab = openTabs.find((t) => t.kind === "file" && layoutTabIds.includes(t.id));
    const chatTab = openTabs.find((t) => t.kind === "chat" && layoutTabIds.includes(t.id));
    if (fileTab && chatTab && fileTab.id !== chatTab.id) {
      splitFocusedGroupWithTab(
        fileTab.id === layout.groups[layout.focusedGroupId]?.activeTabId ? chatTab.id : fileTab.id,
      );
    }
  }, [openTabs, layout, splitFocusedGroupWithTab]);

  const handleDismissChatAlert = useCallback((chatId: string) => dismissCompletionAlert(chatId), []);
  const hasVisibleTabs = collectTabIds(layout).length > 0;

  return (
    <VerseEditorProvider onOpenFile={handleOpenFile} onRequestSplit={handleRequestSplit} projectPath={projectPath}>
      <FocusViewMain
        openTabs={openTabs}
        layout={layout}
        setLayout={setLayout}
        closeTab={closeTab}
        killTerminal={killTerminal}
        reopenTerminalTab={reopenTerminalTab}
        parkedTabs={parkedTabs}
        activateTabInGroup={activateTabInGroup}
        setFocusedGroup={setFocusedGroup}
        reorderTabsInGroup={reorderTabsInGroup}
        dropTabOnGroup={dropTabOnGroup}
        toggleGroupLockInLayout={toggleGroupLockInLayout}
        activeFilePath={activeFilePath}
        projectPath={projectPath}
        hasProject={hasProject}
        listener={listener}
        project={project}
        handleCloseWindow={handleCloseWindow}
        hasVisibleTabs={hasVisibleTabs}
        hiddenChatTabs={hiddenChatTabs}
        hiddenFileTabs={hiddenFileTabs}
        hiddenTerminalTabs={hiddenTerminalTabs}
        onRestartTerminal={handleRestartTerminal}
        allChats={allChats}
        folders={folders}
        runningChatIds={runningChatIds}
        handleOpenChat={handleOpenChat}
        openFileTab={openFileTab}
        handleDismissChatAlert={handleDismissChatAlert}
        notifyMainFocusTabActive={notifyMainFocusTabActive}
        defaultTerminalShell={defaultTerminalShell}
        setDefaultTerminalShell={setDefaultTerminalShell}
        handleNewTerminal={handleNewTerminal}
      />
      <DuckyProfileModal
        open={duckyModal !== null}
        state={duckyModal}
        onClose={handleCloseDuckyModal}
        onCreated={handleDuckyCreated}
        onSaved={load}
      />
    </VerseEditorProvider>
  );
}

function FocusViewMain({
  openTabs,
  layout,
  setLayout,
  closeTab,
  killTerminal,
  reopenTerminalTab,
  parkedTabs,
  activateTabInGroup,
  setFocusedGroup,
  reorderTabsInGroup,
  dropTabOnGroup,
  toggleGroupLockInLayout,
  activeFilePath,
  projectPath,
  hasProject,
  listener,
  project,
  handleCloseWindow,
  hasVisibleTabs,
  hiddenChatTabs,
  hiddenFileTabs,
  hiddenTerminalTabs,
  onRestartTerminal,
  allChats,
  folders,
  runningChatIds,
  handleOpenChat,
  openFileTab,
  handleDismissChatAlert,
  notifyMainFocusTabActive,
  defaultTerminalShell,
  setDefaultTerminalShell,
  handleNewTerminal,
}: {
  openTabs: EditorTab[];
  layout: ReturnType<typeof useFocusWindowTabs>["layout"];
  setLayout: ReturnType<typeof useFocusWindowTabs>["setLayout"];
  closeTab: (tabId: string) => void;
  killTerminal: (tabId: string) => void;
  reopenTerminalTab: (tabId: string) => boolean;
  parkedTabs: EditorTab[];
  activateTabInGroup: ReturnType<typeof useFocusWindowTabs>["activateTabInGroup"];
  setFocusedGroup: ReturnType<typeof useFocusWindowTabs>["setFocusedGroup"];
  reorderTabsInGroup: ReturnType<typeof useFocusWindowTabs>["reorderTabsInGroup"];
  dropTabOnGroup: ReturnType<typeof useFocusWindowTabs>["dropTabOnGroup"];
  toggleGroupLockInLayout: ReturnType<typeof useFocusWindowTabs>["toggleGroupLockInLayout"];
  activeFilePath: string | undefined;
  projectPath: string;
  folders: import("../types/panel").FolderItem[];
  hasProject: boolean;
  listener: ReturnType<typeof useListenerStatus>;
  project: { name: string; path: string; slug: string };
  handleCloseWindow: () => void;
  hasVisibleTabs: boolean;
  hiddenChatTabs: ChatTab[];
  hiddenFileTabs: EditorTab[];
  hiddenTerminalTabs: EditorTab[];
  onRestartTerminal: (tab: EditorTab) => void;
  allChats: ChatTab[];
  runningChatIds: Set<string>;
  handleOpenChat: (chat: ChatTab) => void;
  openFileTab: (path: string, name: string) => void;
  handleDismissChatAlert: (chatId: string) => void;
  notifyMainFocusTabActive: (focusId: string, title: string) => void;
  defaultTerminalShell: TerminalShell;
  setDefaultTerminalShell: React.Dispatch<React.SetStateAction<TerminalShell>>;
  handleNewTerminal: () => Promise<void>;
}) {
  const verseEditor = useVerseEditorOptional();
  const completionAlertChatIds = useCompletionAlertChatIds();
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;

  const notifyTabActive = useCallback(
    (tabId: string) => {
      const tab = openTabsRef.current.find((t) => t.id === tabId);
      if (tab) notifyMainFocusTabActive(tab.id, tab.name);
    },
    [notifyMainFocusTabActive],
  );

  const handleTabSelect = useCallback(
    (tabId: string) => {
      notifyTabActive(tabId);
    },
    [notifyTabActive],
  );

  const handleActivateTab = useCallback(
    (groupId: string, tabId: string) => {
      activateTabInGroup(groupId, tabId);
      notifyTabActive(tabId);
    },
    [activateTabInGroup, notifyTabActive],
  );

  const handleFocusGroup = useCallback(
    (groupId: string) => {
      setFocusedGroup(groupId);
      const tabId = layout.groups[groupId]?.activeTabId;
      if (tabId) notifyTabActive(tabId);
    },
    [setFocusedGroup, layout.groups, notifyTabActive],
  );

  // Every tab kind, not just the handful listed here before ("terminal" was the
  // fallback, so plugin/plan/usage tabs were adopted under the wrong name).
  const titleFromTabId = useCallback(
    (tabId: string) => focusIdToEditorTab(tabId, "")?.name || tabId,
    [],
  );

  const handleDropTab = useCallback(
    (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => {
      const external = !layout.groups[sourceGroupId];
      dropTabOnGroup(targetGroupId, tabId, sourceGroupId, zone);
      if (external) {
        const tab = openTabsRef.current.find((t) => t.id === tabId);
        void getApi()?.adopt_tab_into_this_focus_window(tabId, tab?.name ?? titleFromTabId(tabId));
      }
    },
    [dropTabOnGroup, layout.groups, titleFromTabId],
  );

  const [unsavedPrompt, setUnsavedPrompt] = useState<{ tabId: string; path: string; name: string } | null>(null);
  const [unsavedSaving, setUnsavedSaving] = useState(false);

  const revealEditorPosition = useCallback(
    (path: string, line: number, column: number) => {
      if (verseEditor?.revealIfOpen(path, line, column)) return;
      verseEditor?.requestReveal(path, line, column);
    },
    [verseEditor],
  );

  const handleOpenFileAt = useCallback(
    (path: string, name: string, options?: { line?: number }) => {
      openProjectFileAt(path, name, (p, n) => openSidebarProjectFile(p, n, openFileTab), revealEditorPosition, options);
    },
    [openFileTab, revealEditorPosition],
  );

  const handleTabActivated = useCallback(
    (tab: EditorTab) => {
      focusActivatedEditorTab(tab, revealEditorPosition);
    },
    [revealEditorPosition],
  );

  const requestCloseTab = useCallback(
    (tabId: string) => {
      const tab = openTabs.find((t) => t.id === tabId);
      if (tab?.kind === "file" && tab.path && verseEditor?.isPathDirty(tab.path.replace(/\\/g, "/"))) {
        setUnsavedPrompt({ tabId, path: tab.path.replace(/\\/g, "/"), name: tab.name });
        return;
      }
      void closeTab(tabId);
    },
    [openTabs, verseEditor, closeTab],
  );

  const handleSaveTab = useCallback(
    (tab: EditorTab) => {
      if (tab.kind === "file" && tab.path && verseEditor) void verseEditor.savePath(tab.path.replace(/\\/g, "/"));
    },
    [verseEditor],
  );

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
        onCloseTerminal={(tabId) => void killTerminal(tabId)}
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
      <VerseWorkflowBridge enabled={hasProject} />
      <FocusShell
        header={
          <FocusHeaderBar
            onCloseWindow={handleCloseWindow}
            isOnline={listener.online}
            isWedged={listener.wedged}
            statusText={listener.status_text}
            listenerStatus={listener}
            projectMatch={listener.project_match}
            project={project}
            hasProject={hasProject}
            layout={layout}
            openTabs={openTabs}
            onCloseTab={requestCloseTab}
            onActivateTab={handleActivateTab}
            onReorderTabs={reorderTabsInGroup}
            onSaveTab={handleSaveTab}
            onTabSelect={handleTabSelect}
            onTabActivated={handleTabActivated}
            onDismissChatAlert={handleDismissChatAlert}
            allChats={allChats}
            runningChatIds={runningChatIds}
            completionAlertChatIds={completionAlertChatIds}
            onToggleGroupLock={toggleGroupLockInLayout}
          />
        }
      >
        <div className="focus-editor-area">
          <div className="focus-header-reveal-band drag-region app-drag-surface" aria-hidden="true" />
          {!hasVisibleTabs ? (
            // Still a drop target: an empty focus window is exactly when you want to
            // drag a tab into it, and there is no group pane mounted to catch the drop.
            <EditorTabDropSurface
              targetGroupId={layout.focusedGroupId}
              onDropTab={handleDropTab}
              className="editor-tab-drop-surface--fill"
            >
              <div className="ui-status-muted">No tabs open.</div>
            </EditorTabDropSurface>
          ) : (
            <SplitEditorLayout
              folders={folders}
              variant="focus"
              layout={layout}
              openTabs={openTabs}
              onLayoutChange={setLayout}
              hiddenChatTabs={hiddenChatTabs}
              hiddenFileTabs={hiddenFileTabs}
              hiddenTerminalTabs={hiddenTerminalTabs}
              onRestartTerminal={onRestartTerminal}
              allChats={allChats}
              contextFilePath={activeFilePath}
              runningChatIds={runningChatIds}
              onOpenChat={handleOpenChat}
              onOpenFile={handleOpenFileAt}
              onDismissChatAlert={handleDismissChatAlert}
              onCloseTab={requestCloseTab}
              onReorderTabs={reorderTabsInGroup}
              onDropTab={handleDropTab}
              onFocusGroup={handleFocusGroup}
              onActivateTab={handleActivateTab}
              onSaveTab={handleSaveTab}
              onTabSelect={handleTabSelect}
              onTabActivated={handleTabActivated}
              onToggleGroupLock={toggleGroupLockInLayout}
            />
          )}
        </div>
      </FocusShell>
      {unsavedPrompt ? (
        <UnsavedChangesModal
          fileName={unsavedPrompt.name}
          saving={unsavedSaving}
          onSave={() => {
            void (async () => {
              if (!verseEditor) return;
              setUnsavedSaving(true);
              const ok = await verseEditor.savePath(unsavedPrompt.path);
              setUnsavedSaving(false);
              if (!ok) return;
              const tabId = unsavedPrompt.tabId;
              setUnsavedPrompt(null);
              void closeTab(tabId);
            })();
          }}
          onDiscard={() => {
            const tabId = unsavedPrompt.tabId;
            setUnsavedPrompt(null);
            void closeTab(tabId);
          }}
          onCancel={() => setUnsavedPrompt(null)}
        />
      ) : null}
    </>
  );
}

export function FocusView({ focusId }: FocusViewProps) {
  // Always mount the shell — birth-tab parse can fail after URL double-encoding
  // (settings%3Amain) or for new kinds; handlers still need to live for tab open events.
  return (
    <AppearanceProvider>
      <DuckyCatalogProvider>
        <ThemeProvider />
        <PanelApiGate>
          <ConfirmModalProvider>
          <TerminalCommandApprovalProvider>
            <AppHeaderActionsProvider>
              <AskAiBridgeProvider>
              <ProblemsDuckyBridgeProvider>
              <QuickOpenBridgeProvider>
                <TerminalsSettingsProvider>
                  <FocusViewContent focusId={focusId} />
                </TerminalsSettingsProvider>
              </QuickOpenBridgeProvider>
              </ProblemsDuckyBridgeProvider>
              </AskAiBridgeProvider>
            </AppHeaderActionsProvider>
          </TerminalCommandApprovalProvider>
          </ConfirmModalProvider>
        </PanelApiGate>
      </DuckyCatalogProvider>
    </AppearanceProvider>
  );
}

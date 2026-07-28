import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { EditorDropZone, EditorGroup, EditorTab } from "../types/panel";
import { EditorTabs } from "./EditorTabs";
import { FileEditorPane } from "./FileEditorPane";
import { ChatPane } from "./ChatPane";
import { PlanPane } from "./PlanPane";
import { ProviderUsageReport } from "./usage/ProviderUsageReport";
import { TerminalPane } from "../terminal/TerminalPane";
import { SettingsView } from "../views/SettingsView";
import { GroupChatPanel } from "./groupchat/GroupChatPanel";
import { discordBotIdFromTab } from "../types/panel";
import { PluginWebviewPane } from "../plugin-ui";
import { DucktactoeChatShell } from "../plugin-ui/DucktactoeChatShell";
import { isDucktactoeChat } from "../plugin-ui/ducktactoeBoardChat";
import { VerseTranslatedPane } from "./VerseTranslatedPane";
import { DuckyProfileTabPane } from "./ducky/DuckyProfileTabPane";
import { useTerminalsSettings } from "../contexts/TerminalsSettingsContext";
import { duckyProfileIdFromTab } from "../types/panel";
import { dropZoneFromPointer } from "../utils/editorLayoutOps";
import { getEditorTabDragData, getEditorTabGroupDragData, handleExternalTabDrop, isEditorTabDrag, markEditorTabDropped } from "../utils/editorTabDrag";
import { getApi } from "../hooks/usePanelApi";
import { dragHasOsFiles, isChatAttachDropTarget, OPEN_EXTERNAL_TARGET } from "../utils/osFileDrag";
import type { ChatTab, FolderItem } from "../types/panel";
import {
  getSidebarEditorDropPreview,
  subscribeSidebarEditorDropPreview,
} from "../utils/sidebarDragOut";

interface EditorGroupPaneProps {
  group: EditorGroup;
  openTabs: EditorTab[];
  isFocused: boolean;
  variant?: "default" | "focus";
  allChats: ChatTab[];
  folders?: FolderItem[];
  contextFilePath?: string;
  runningChatIds: Set<string>;
  onOpenChat: (chat: ChatTab) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  onOpenPlan?: (chatId: string, title?: string) => void;
  onDismissChatAlert?: (chatId: string) => void;
  onFocusGroup: (groupId: string) => void;
  onActivateTab: (groupId: string, tabId: string) => void;
  onCloseTab: (tabId: string) => void;
  onReorderTabs: (
    groupId: string,
    draggedId: string,
    targetId: string,
    insertBefore: boolean,
    sourceGroupId?: string,
  ) => void;
  onDropTab: (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => void;
  onFocusTab?: (tab: EditorTab, at?: { screenX: number; screenY: number }) => void;
  onSaveTab?: (tab: EditorTab) => void;
  onTabSelect?: (tabId: string) => void;
  onTabActivated?: (tab: EditorTab) => void;
  onPromoteTab?: (tabId: string) => void;
  onRevealFileInSidebar?: (path: string) => void;
  onRevealFileInExplorer?: (path: string) => void;
  completionAlertChatIds?: ReadonlySet<string>;
  onRestartTerminal?: (tab: EditorTab) => void;
  onToggleGroupLock?: (groupId: string) => void;
}

export function EditorGroupPane({
  group,
  openTabs,
  isFocused,
  variant = "default",
  allChats,
  folders = [],
  contextFilePath,
  runningChatIds,
  onOpenChat,
  onOpenFile,
  onOpenPlan,
  onDismissChatAlert,
  onFocusGroup,
  onActivateTab,
  onCloseTab,
  onReorderTabs,
  onDropTab,
  onFocusTab,
  onSaveTab,
  onTabSelect,
  onTabActivated,
  onPromoteTab,
  onRevealFileInSidebar,
  onRevealFileInExplorer,
  completionAlertChatIds,
  onToggleGroupLock,
}: EditorGroupPaneProps) {
  const [dropZone, setDropZone] = useState<EditorDropZone | null>(null);
  // An OS file is being dragged over the editor body → show the "Open file" overlay.
  const [externalOver, setExternalOver] = useState(false);
  const externalArmedRef = useRef(false);
  const { enabled: terminalsEnabled } = useTerminalsSettings();
  const paneVariant = variant === "focus" ? "focus" : "default";
  // dnd-kit sidebar → editor: show the same split squares as HTML5 tab drags.
  const sidebarPreview = useSyncExternalStore(
    subscribeSidebarEditorDropPreview,
    getSidebarEditorDropPreview,
    () => null,
  );
  const sidebarZone =
    sidebarPreview?.groupId === group.id ? sidebarPreview.zone : null;
  const visibleDropZone = dropZone ?? sidebarZone;

  // Report the sentinel once per drag-enter (not on every dragover tick).
  const armExternalDrop = useCallback(() => {
    if (externalArmedRef.current) return;
    externalArmedRef.current = true;
    setExternalOver(true);
    getApi()?.set_import_drop_target?.(OPEN_EXTERNAL_TARGET)?.catch?.(() => {});
  }, []);

  const disarmExternalDrop = useCallback((clearTarget: boolean) => {
    if (!externalArmedRef.current) return;
    externalArmedRef.current = false;
    setExternalOver(false);
    // On drop, leave the target for the native handler to consume; only clear it when
    // the drag leaves the editor without dropping.
    if (clearTarget) getApi()?.set_import_drop_target?.("")?.catch?.(() => {});
  }, []);

  // dragleave/drop often miss when a tab drag cancels or ends on the tab strip —
  // without dragend the blue split overlay stays stuck over the pane.
  useEffect(() => {
    const clear = () => {
      setDropZone(null);
      disarmExternalDrop(true);
    };
    document.addEventListener("dragend", clear);
    return () => document.removeEventListener("dragend", clear);
  }, [disarmExternalDrop]);

  const groupTabs = useMemo(
    () =>
      group.tabIds
        .map((id) => openTabs.find((t) => t.id === id))
        .filter((t): t is EditorTab => !!t)
        .filter((t) => terminalsEnabled || t.kind !== "terminal"),
    [group.tabIds, openTabs, terminalsEnabled],
  );

  const activeTab = group.activeTabId ? openTabs.find((t) => t.id === group.activeTabId) : undefined;

  // Interacting inside the editor/chat body (clicking or typing into it) pins a preview
  // tab — "working in it" makes it permanent. Capture phase so Monaco/inputs can't
  // swallow the event first.
  const promoteActiveIfPreview = useCallback(() => {
    if (activeTab?.preview && onPromoteTab) onPromoteTab(activeTab.id);
  }, [activeTab, onPromoteTab]);

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      if (dragHasOsFiles(e.dataTransfer)) {
        // Chat pane owns this drag as an attachment upload — hide "Open file" but do
        // not clear the import target (chat sets CHAT_ATTACH_TARGET for the native drop).
        if (isChatAttachDropTarget(e.target)) {
          e.preventDefault();
          if (externalArmedRef.current) {
            externalArmedRef.current = false;
            setExternalOver(false);
          }
          return;
        }
        // OS file → arm "Open file". preventDefault keeps the drop allowed; never
        // stopPropagation (the drop must reach the native document handler that reads
        // the OS path). No zone highlight — external opens ignore split zones.
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        armExternalDrop();
        return;
      }
      if (!isEditorTabDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      const rect = e.currentTarget.getBoundingClientRect();
      setDropZone(dropZoneFromPointer(rect, e.clientX, e.clientY));
    },
    [armExternalDrop],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      if (dragHasOsFiles(e.dataTransfer)) {
        // Let it bubble to pywebview's native document drop handler, which reads the OS
        // path and fires ducky:external-files-open. Just drop the overlay; the window
        // guard already preventDefaulted. Do NOT stopPropagation or clear the target.
        disarmExternalDrop(false);
        return;
      }
      if (!isEditorTabDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      if (handleExternalTabDrop(e)) {
        setDropZone(null);
        return;
      }
      markEditorTabDropped();
      const tabId = getEditorTabDragData(e.dataTransfer);
      const sourceGroupId = getEditorTabGroupDragData(e.dataTransfer);
      const rect = e.currentTarget.getBoundingClientRect();
      const zone = dropZone ?? dropZoneFromPointer(rect, e.clientX, e.clientY);
      setDropZone(null);
      if (!tabId || !sourceGroupId) return;
      onDropTab(group.id, tabId, sourceGroupId, zone);
    },
    [dropZone, group.id, onDropTab, disarmExternalDrop],
  );

  const renderContent = () => {
    if (!activeTab) {
      return <div className="editor-group-empty">Drop a tab here</div>;
    }
    if (activeTab.kind === "file" && activeTab.path) {
      return <FileEditorPane relativePath={activeTab.path} variant={paneVariant} />;
    }
    if (activeTab.kind === "chat" && activeTab.chatId) {
      const found = allChats.find((c) => c.id === activeTab.chatId);
      const chat: ChatTab = found
        ? { ...found, isGroup: found.isGroup || activeTab.isGroup }
        : {
            id: activeTab.chatId,
            name: activeTab.name,
            duckyStyle: activeTab.duckyStyle,
            isGroup: Boolean(activeTab.isGroup),
          };
      if (isDucktactoeChat(chat)) {
        return (
          <DucktactoeChatShell
            key={chat.id}
            chat={chat}
            allChats={allChats}
            folders={folders}
            contextFilePath={contextFilePath}
            runningChatIds={runningChatIds}
            variant={paneVariant}
            onOpenChat={onOpenChat}
            onOpenFile={onOpenFile}
            onOpenPlan={onOpenPlan}
            onDismissChatAlert={onDismissChatAlert}
          />
        );
      }
      return (
        <ChatPane
          key={chat.id}
          chat={chat}
          visible
          variant={paneVariant}
          allChats={allChats}
          folders={folders}
          contextFilePath={contextFilePath}
          onOpenChat={onOpenChat}
          onOpenFile={onOpenFile}
          onOpenPlan={onOpenPlan}
          isAgentRunning={runningChatIds.has(chat.id)}
          onEngage={onDismissChatAlert ? () => onDismissChatAlert(chat.id) : undefined}
        />
      );
    }
    if (activeTab.kind === "plan" && activeTab.chatId) {
      const chat =
        allChats.find((c) => c.id === activeTab.chatId) ??
        ({ id: activeTab.chatId, name: activeTab.name } as ChatTab);
      return (
        <PlanPane
          chatId={chat.id}
          chatName={activeTab.name || chat.name}
          projectRoot={activeTab.projectRoot}
          onOpenChat={onOpenChat}
        />
      );
    }
    if (activeTab.kind === "usage" && activeTab.path) {
      const label = activeTab.name.replace(/\s+usage$/i, "") || activeTab.path;
      return (
        <div className="provider-usage-tab-pane">
          <ProviderUsageReport providerId={activeTab.path} label={label} days={7} />
        </div>
      );
    }
    if (activeTab.kind === "terminal" && activeTab.terminalSessionId && activeTab.terminalWsUrl) {
      if (!terminalsEnabled) return null;
      return (
        <TerminalPane
          sessionId={activeTab.terminalSessionId}
          wsUrl={activeTab.terminalWsUrl}
          visible
          variant={paneVariant}
        />
      );
    }
    if (activeTab.kind === "settings") {
      return <SettingsView />;
    }
    if (activeTab.kind === "discord") {
      // key forces a clean mount per bot — avoids stale "Connected as …" from the prior tab.
      return (
        <GroupChatPanel
          key={activeTab.id}
          variant="full"
          botId={discordBotIdFromTab(activeTab.id)}
        />
      );
    }
    if (activeTab.kind === "plugin") {
      return (
        <PluginWebviewPane
          tabId={activeTab.id}
          chatOverlay={{
            allChats,
            runningChatIds,
            onOpenChat,
            onOpenFile,
            onOpenPlan,
            onDismissChatAlert,
          }}
        />
      );
    }
    if (activeTab.kind === "verse-translated" && activeTab.path) {
      return <VerseTranslatedPane relativePath={activeTab.path} />;
    }
    if (activeTab.kind === "ducky-profile") {
      const profileId = activeTab.path || duckyProfileIdFromTab(activeTab.id);
      if (!profileId) return null;
      return (
        <DuckyProfileTabPane
          profileId={profileId}
          onCloseTab={() => onCloseTab(activeTab.id)}
        />
      );
    }
    return null;
  };

  return (
    <div
      className={`editor-group${isFocused ? " is-focused" : ""}${variant === "focus" ? "" : " no-drag"}`}
      data-editor-group-id={group.id}
      onMouseDown={() => onFocusGroup(group.id)}
    >
      {variant !== "focus" ? (
        <div className="editor-tab-bar-row editor-group-tab-bar">
          <div className="editor-tab-bar-tabs">
            <EditorTabs
              groupId={group.id}
              tabs={groupTabs}
              activeTabId={group.activeTabId}
              closeTab={onCloseTab}
              onActivate={(tabId) => onActivateTab(group.id, tabId)}
              onReorderTabs={(draggedId, targetId, insertBefore, sourceGroupId) =>
                onReorderTabs(group.id, draggedId, targetId, insertBefore, sourceGroupId)
              }
              onFocusTab={onFocusTab}
              onSaveTab={onSaveTab}
              onTabSelect={onTabSelect}
              onTabActivated={onTabActivated}
              onPromoteTab={onPromoteTab}
              onRevealFileInSidebar={onRevealFileInSidebar}
              onRevealFileInExplorer={onRevealFileInExplorer}
              completionAlertChatIds={completionAlertChatIds}
              onDismissChatAlert={onDismissChatAlert}
              allChats={allChats}
              runningChatIds={runningChatIds}
              variant={variant}
              groupLocked={!!group.locked}
              onToggleGroupLock={onToggleGroupLock ? () => onToggleGroupLock(group.id) : undefined}
            />
          </div>
        </div>
      ) : null}
      <div
        className="no-drag editor-group-body"
        onMouseDownCapture={promoteActiveIfPreview}
        onKeyDownCapture={promoteActiveIfPreview}
        onDragOver={handleDragOver}
        onDragLeave={(e) => {
          if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
            setDropZone(null);
            disarmExternalDrop(true);
          }
        }}
        onDrop={handleDrop}
      >
        {visibleDropZone ? (
          <div className={`editor-drop-overlay editor-drop-overlay--${visibleDropZone}`} />
        ) : null}
        {externalOver ? (
          <div className="editor-drop-overlay editor-drop-overlay--center editor-drop-overlay--external">
            Open file
          </div>
        ) : null}
        {renderContent()}
      </div>
    </div>
  );
}

import { useCallback, useMemo, useState } from "react";
import { Header } from "./Header";
import { EditorTabs } from "./EditorTabs";
import { getApi } from "../hooks/usePanelApi";
import { useTerminalsSettings } from "../contexts/TerminalsSettingsContext";
import type { ChatTab, EditorLayoutState, EditorTab, ListenerStatus } from "../types/panel";

interface FocusHeaderBarProps {
  onCloseWindow: () => void;
  onPinnedChange?: (pinned: boolean) => void;
  isOnline: boolean;
  isWedged?: boolean;
  statusText?: string;
  listenerStatus?: ListenerStatus;
  projectMatch?: boolean;
  project?: { name: string; path: string; slug: string };
  hasProject: boolean;
  layout: EditorLayoutState;
  openTabs: EditorTab[];
  onCloseTab: (tabId: string) => void;
  onActivateTab: (groupId: string, tabId: string) => void;
  onReorderTabs: (
    groupId: string,
    draggedId: string,
    targetId: string,
    insertBefore: boolean,
    sourceGroupId?: string,
  ) => void;
  onSaveTab?: (tab: EditorTab) => void;
  onTabSelect?: (tabId: string) => void;
  onTabActivated?: (tab: EditorTab) => void;
  onDismissChatAlert?: (chatId: string) => void;
  allChats: ChatTab[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  onToggleGroupLock?: (groupId: string) => void;
}

export function FocusHeaderBar({
  onCloseWindow,
  onPinnedChange,
  isOnline,
  isWedged = false,
  statusText,
  listenerStatus,
  projectMatch,
  project = { name: "", path: "", slug: "" },
  hasProject,
  layout,
  openTabs,
  onCloseTab,
  onActivateTab,
  onReorderTabs,
  onSaveTab,
  onTabSelect,
  onTabActivated,
  onDismissChatAlert,
  allChats,
  runningChatIds,
  completionAlertChatIds,
  onToggleGroupLock,
}: FocusHeaderBarProps) {
  const [pinned, setPinned] = useState(false);
  const { enabled: terminalsEnabled } = useTerminalsSettings();

  const focusedGroup = layout.groups[layout.focusedGroupId];

  const groupTabs = useMemo(
    () =>
      focusedGroup
        ? focusedGroup.tabIds
            .map((id) => openTabs.find((t) => t.id === id))
            .filter((t): t is EditorTab => !!t)
            .filter((t) => terminalsEnabled || t.kind !== "terminal")
        : [],
    [focusedGroup, openTabs, terminalsEnabled],
  );

  const setPinnedState = useCallback(
    (next: boolean) => {
      setPinned(next);
      onPinnedChange?.(next);
    },
    [onPinnedChange],
  );

  const handleProblemsOpenChange = useCallback(
    (open: boolean) => setPinnedState(open),
    [setPinnedState],
  );

  return (
    <div className={`focus-header-zone${pinned ? " is-pinned" : ""}`}>
      <div className="focus-header-chrome">
        <Header
          variant="focus"
          isOnline={isOnline}
          isWedged={isWedged}
          statusText={statusText}
          listenerStatus={listenerStatus}
          projectMatch={projectMatch}
          project={project}
          layoutMode="full"
          cycleLayoutMode={() => {}}
          hasProject={hasProject}
          showQuickOpen={hasProject}
          onCloseWindow={onCloseWindow}
          onProblemsOpenChange={handleProblemsOpenChange}
        />
        {focusedGroup && groupTabs.length > 0 ? (
          <div className="focus-header-tabs editor-tab-bar-row editor-group-tab-bar drag-region app-drag-surface">
            <div className="editor-tab-bar-tabs">
              <EditorTabs
                groupId={focusedGroup.id}
                tabs={groupTabs}
                activeTabId={focusedGroup.activeTabId}
                closeTab={onCloseTab}
                onActivate={(tabId) => onActivateTab(focusedGroup.id, tabId)}
                onReorderTabs={(draggedId, targetId, insertBefore, sourceGroupId) =>
                  onReorderTabs(focusedGroup.id, draggedId, targetId, insertBefore, sourceGroupId)
                }
                onSaveTab={onSaveTab}
                onTabSelect={onTabSelect}
                onTabActivated={onTabActivated}
                completionAlertChatIds={completionAlertChatIds}
                onDismissChatAlert={onDismissChatAlert}
                allChats={allChats}
                runningChatIds={runningChatIds}
                variant="focus"
                groupLocked={!!focusedGroup.locked}
                onToggleGroupLock={
                  onToggleGroupLock ? () => onToggleGroupLock(focusedGroup.id) : undefined
                }
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function useFocusCloseWindow() {
  return useCallback(() => {
    const api = getApi();
    if (api) void api.close_this_window();
  }, []);
}

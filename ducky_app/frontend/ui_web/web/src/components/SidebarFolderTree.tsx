import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { Icons } from "../icons/Icons";
import type { FolderItem } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import type { EditorTabHoverCardPlacement } from "../hooks/useEditorTabHoverCard";
import {
  pluginContributesSettingsTab,
  usePluginContributions,
} from "../hooks/usePluginContributions";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import {
  openTranslatedChat,
  requestChatTranslateWalk,
} from "../navigation/openTranslatedChat";
import {
  isAutoTranslateChat,
  toggleAutoTranslateChat,
} from "../navigation/tabTranslatePrefs";
import { isEnglishLang } from "../views/settings/translationLanguages";
import type { DockSide } from "../workspace/workspaceDockStorage";
import { ContextMenu, useContextMenuState } from "./ContextMenu";
import {
  contextMenuSeparator,
  duckyTreeCreateItems,
} from "../utils/sidebarContextMenuItems";
import {
  dragId,
  duckiesFoldersForDisplay,
  expandFoldersById,
  findFolderById,
  GLOBAL_AGENTS_LABEL,
  GLOBAL_PROJECT_SLUG,
  globalAgentsFolder,
  isGlobalProjectFolderId,
  isProjectFolderId,
  parseDragId,
} from "../utils/sidebarTree";
import {
  buildDuckiesIndex,
  duckiesCreateTarget,
  duckiesDropPolicy,
  duckiesLayoutFor,
  duckiesPlaces,
  isProjectNode,
  planDuckiesDrop,
  planDuckiesRestore,
  type DuckiesMovePlan,
  type DuckiesTreeData,
} from "../utils/duckiesTreeModel";
import { duckiesLayoutHold } from "../utils/duckiesLayoutHold";
import { useTreeDnd } from "../tree-dnd/useTreeDnd";
import { topLevelSources } from "../tree-dnd/treeMove";
import { keepTreeFocus } from "../tree-dnd/treeFocus";
import { useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import {
  collectVisibleDuckyIds,
  duckyNameMatches,
  shouldShowChat,
  shouldShowFolder,
} from "../utils/duckyTreeFilter";
import { anyRunningUnderFolder } from "../utils/duckyTreeBusy";
import type { EditorDropZone } from "../types/panel";
import { formatRelativeTime } from "../utils/formatRelativeTime";
import {
  chatNestDefaultExpanded,
  loadChatNestExpanded,
  saveChatNestExpanded,
} from "../utils/chatNestExpand";
import { SidebarTreeChildren } from "./sidebar/SidebarTreeChildren";
import { SidebarTreeRow } from "./sidebar/SidebarTreeRow";
import { LiveChatDot } from "../voice/LiveChatMark";
import { useIsLiveChat } from "../voice/useLiveChatPresence";
import { ChatTabHoverCard } from "./editor/ChatTabHoverCard";
import { FolderTabHoverCard } from "./editor/FolderTabHoverCard";
import {
  renameInputProps,
  SidebarHoverActions,
} from "./sidebar/sidebarTreeShared";
import {
  emptySelection,
  rangeSelection,
  selectOnly,
  toggleSelection,
  type ExplorerSelection,
} from "../utils/fileTreeSelection";

const ChatTreeHoverPlacementContext = createContext<EditorTabHoverCardPlacement>("right");
const DuckiesCompactContext = createContext(false);
const DuckiesCurrentProjectContext = createContext("");

/** A row's view of the tree-wide multi-selection, so it can offer "Delete ALL". */
type RowSelectionCtx = {
  selected: ReadonlySet<string>;
  /** Right-clicking a row outside the selection selects it (VS Code / Content). */
  onContextSelect: (rowId: string) => void;
};

const RowSelectionContext = createContext<RowSelectionCtx>({
  selected: new Set<string>(),
  onContextSelect: () => {},
});

function useRowDeleteCount(rowId: string): number {
  const { selected } = useContext(RowSelectionContext);
  return selected.has(rowId) && selected.size > 1 ? selected.size : 1;
}

/** One row from the duckies tree targeted by a delete. */
export type DuckyDeleteTarget = { kind: "folder" | "chat"; id: string; name: string };

type EditTarget = { kind: "folder" | "chat"; id: string; value: string };
type SelectMods = { ctrl: boolean; shift: boolean };

function shortModelLabel(model?: string): string {
  const raw = (model || "").trim();
  if (!raw) return "—";
  const slash = raw.lastIndexOf("/");
  return slash >= 0 ? raw.slice(slash + 1) : raw;
}

function chatLlmLabel(
  chat: FolderItem["chats"][number],
  agentLabels: Record<string, string>,
): string {
  const model = shortModelLabel(chat.model);
  const aid = (chat.codingAgent || "").trim().toLowerCase().replace(/-/g, "_");
  if (!aid || aid === "ducky") return model;
  const label = (agentLabels[aid] || aid.replace(/_/g, " ")).trim();
  if (!label) return model;
  return model !== "—" ? `${label} · ${model}` : label;
}

function chatMetaLine(
  chat: FolderItem["chats"][number],
  agentLabels: Record<string, string>,
): string {
  const files = chat.fileCount ?? 0;
  const tools = chat.toolCallCount ?? 0;
  const parts = [
    `${files} ${files === 1 ? "File" : "Files"}`,
    `${tools} toolcalls`,
    chatLlmLabel(chat, agentLabels),
  ];
  const when = formatRelativeTime(chat.updated);
  if (when) parts.push(when);
  return parts.join(" · ");
}

const ChatRow = memo(function ChatRow({
  chat,
  isActive,
  isFocused = false,
  isRunning,
  hasCompletionAlert,
  isEditing,
  editing,
  setEditing,
  editInputRef,
  onSelect,
  onSelectPersistent,
  onModSelect,
  onCommitRename,
  onCancelRename,
  onRename,
  onDelete,
  onFocus,
  onEditDucky,
  isNew,
  archived = false,
  nestToggle,
}: {
  chat: FolderItem["chats"][number];
  isActive: boolean;
  isFocused?: boolean;
  isRunning: boolean;
  hasCompletionAlert: boolean;
  isEditing: boolean;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  onSelect: () => void;
  onSelectPersistent?: () => void;
  onModSelect?: (mods: SelectMods) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onRename: () => void;
  onDelete: () => void;
  onFocus: () => void;
  onEditDucky: () => void;
  isNew: boolean;
  archived?: boolean;
  nestToggle?: { expanded: boolean; onToggle: (e: React.MouseEvent) => void; count: number };
}) {
  const hoverPlacement = useContext(ChatTreeHoverPlacementContext);
  const compact = useContext(DuckiesCompactContext);
  const id = dragId("chat", chat.id);
  const rowSelection = useContext(RowSelectionContext);
  const deleteCount = useRowDeleteCount(id);
  const deleteLabel =
    deleteCount > 1 ? "Delete ALL" : archived ? "Delete permanently" : "Archive";
  const { menu, open, close } = useContextMenuState<void>();
  const pluginContrib = usePluginContributions();
  const agentLabels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const row of pluginContrib.llm_coding_agents || []) {
      const id = String(row.id || "").trim().toLowerCase().replace(/-/g, "_");
      const label = String(row.label || "").trim();
      if (id && label) out[id] = label;
    }
    return out;
  }, [pluginContrib.llm_coding_agents]);
  const { prefs: translationPrefs, setPref: setTranslationPref } = usePluginUiPrefs("translation");
  const uiLang =
    typeof translationPrefs.language === "string" && translationPrefs.language.trim()
      ? translationPrefs.language.trim()
      : "en";
  const showChatTranslate =
    pluginContributesSettingsTab(pluginContrib, "Languages") && !isEnglishLang(uiLang);
  const live = useIsLiveChat(chat.id);

  return (
    <ChatTabHoverCard
      chat={chat}
      isRunning={isRunning}
      hasCompletionAlert={hasCompletionAlert}
      placement={hoverPlacement}
    >
      <SidebarTreeRow
        leading={
          <span className="sidebar-folder-leading">
            {nestToggle ? (
              <button
                type="button"
                className="chevron-icon sidebar-chat-nest-toggle"
                title={nestToggle.expanded ? "Hide nested chats" : `Show ${nestToggle.count} nested chat${nestToggle.count === 1 ? "" : "s"}`}
                aria-expanded={nestToggle.expanded}
                onClick={nestToggle.onToggle}
              >
                <Icons.ChevronDown />
              </button>
            ) : null}
            <span
              className={`sidebar-tree-row-icon sidebar-tree-row-icon--chat${hasCompletionAlert && !isRunning ? " chat-completion-alert" : ""}${chat.isLeader ? " sidebar-tree-row-icon--leader" : ""}${live ? " is-live-chat" : ""}`}
              title={live ? "Live chat" : chat.isLeader ? "Group leader" : undefined}
            >
              {isRunning ? (
                <span className="sidebar-agent-spinner" title="Agent working" />
              ) : (
                <DuckyAvatar
                  styleId={chat.duckyStyle}
                  size={compact ? 22 : DUCKY_AVATAR_SIZES.sidebar}
                  className="ducky-avatar--sidebar"
                />
              )}
              {live ? <LiveChatDot className="live-chat-dot--sidebar" /> : null}
              {chat.isLeader && !isRunning ? (
                <span className="sidebar-leader-badge" title="Group leader" aria-label="Group leader">
                  <Icons.Star />
                </span>
              ) : null}
            </span>
          </span>
        }
        label={chat.name}
        meta={
          compact
            ? undefined
            : chat.isLeader
              ? `Leader · ${chatMetaLine(chat, agentLabels) || "Group"}`
              : chatMetaLine(chat, agentLabels)
        }
        isEditing={isEditing}
        renameInput={
          <input {...renameInputProps(editing, setEditing, editInputRef, onCommitRename, onCancelRename)} />
        }
        actions={
          <SidebarHoverActions
            onRename={onRename}
            onDelete={onDelete}
            activeChat={isActive}
            deleteTitle={deleteLabel}
          />
        }
        contextMenu={
          menu ? (
            <ContextMenu
              x={menu.x}
              y={menu.y}
              onClose={close}
              items={[
                ...(showChatTranslate
                  ? [
                      {
                        id: "translate",
                        label: "Translate",
                        onClick: () => {
                          if (!isAutoTranslateChat(chat.id, translationPrefs)) {
                            toggleAutoTranslateChat(chat.id, translationPrefs, setTranslationPref);
                          }
                          openTranslatedChat(chat);
                          window.setTimeout(() => requestChatTranslateWalk(), 50);
                        },
                      },
                      contextMenuSeparator("sep-translate"),
                    ]
                  : []),
                ...(archived
                  ? []
                  : [{ id: "change-duchy", label: "Change ducky", onClick: () => { close(); onEditDucky(); } }]),
                ...(archived ? [] : [{ id: "focus", label: "Focus", onClick: onFocus }]),
                ...(!archived && chat.parentConvId && !chat.isLeader
                  ? [
                      {
                        id: "make-leader",
                        label: "Make leader",
                        onClick: () => {
                          close();
                          const api = getApi();
                          if (!api?.group_set_leader || !chat.parentConvId) return;
                          void api.group_set_leader(chat.parentConvId, chat.id);
                        },
                      },
                    ]
                  : []),
                { id: "rename", label: "Rename", onClick: onRename },
                { id: "delete", label: deleteLabel, danger: true, onClick: onDelete },
              ]}
            />
          ) : null
        }
        isActive={isActive}
        isFocused={isFocused}
        isNew={isNew}
        dataAttr="data-sidebar-id"
        dataId={id}
        draggable={!archived}
        onClick={(e) => {
          if (isEditing) return;
          if (onModSelect && (e.ctrlKey || e.metaKey || e.shiftKey)) {
            onModSelect({ ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
            return;
          }
          onSelect();
        }}
        onDoubleClick={() => !isEditing && onSelectPersistent?.()}
        onContextMenu={(e) => {
          rowSelection.onContextSelect(id);
          open(e, undefined);
        }}
      />
    </ChatTabHoverCard>
  );
});

type ChatNodeCtx = {
  activeChats: string[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  newlyCreatedIds: Set<string>;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  childrenByParent: Map<string, FolderItem["chats"]>;
  selectedIds: ReadonlySet<string>;
  focusId: string | null;
  onSelectChat: (chat: { id: string; name: string }) => void;
  onSelectChatPersistent?: (chat: { id: string; name: string }) => void;
  onModSelectChat: (chat: { id: string; name: string }, mods: SelectMods) => void;
  onRenameChat: (id: string, name: string) => void;
  onDeleteChat: (id: string, name: string) => void;
  onFocusChat: (chat: { id: string; name: string }) => void;
  onEditDucky: (chat: { id: string; name: string; duckyStyle?: string; duckyPersonality?: string }) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
};

/** A chat row plus any sub-agents spawned from it, nested (indented) beneath. */
function ChatNode({
  chat,
  ctx,
  depth = 0,
}: {
  chat: FolderItem["chats"][number];
  ctx: ChatNodeCtx;
  depth?: number;
}) {
  // Guard against a parent→child→parent cycle blowing the stack.
  const children = depth < 12 ? ctx.childrenByParent.get(chat.id) ?? [] : [];
  const childrenAreSubagents = children.length > 0 && children.every((c) => Boolean(c.isSubagent));
  const [expanded, setExpanded] = useState(() =>
    loadChatNestExpanded(chat.id, chatNestDefaultExpanded(childrenAreSubagents)),
  );
  useEffect(() => {
    // New children appear after spawn — keep prior expand choice.
    if (children.length === 0) return;
    setExpanded((prev) => prev);
  }, [children.length]);

  const toggleNest = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      setExpanded((prev) => {
        const next = !prev;
        saveChatNestExpanded(chat.id, next);
        return next;
      });
    },
    [chat.id],
  );

  const rowId = dragId("chat", chat.id);
  return (
    <div
      className={`sidebar-tree-branch ${children.length > 0 && !expanded ? "sidebar-tree-branch-collapsed" : ""}`}
      data-tree-node={rowId}
    >
      <ChatRow
        chat={chat}
        isActive={ctx.activeChats.includes(chat.id) || ctx.selectedIds.has(rowId)}
        isFocused={ctx.focusId === rowId}
        isRunning={ctx.runningChatIds.has(chat.id)}
        hasCompletionAlert={ctx.completionAlertChatIds?.has(chat.id) ?? false}
        isEditing={ctx.editing?.kind === "chat" && ctx.editing.id === chat.id}
        editing={ctx.editing}
        setEditing={ctx.setEditing}
        editInputRef={ctx.editInputRef}
        onSelect={() => ctx.onSelectChat(chat)}
        onSelectPersistent={() => (ctx.onSelectChatPersistent ?? ctx.onSelectChat)(chat)}
        onModSelect={(mods) => ctx.onModSelectChat(chat, mods)}
        onCommitRename={ctx.onCommitRename}
        onCancelRename={ctx.onCancelRename}
        onRename={() => ctx.onRenameChat(chat.id, chat.name)}
        onDelete={() => ctx.onDeleteChat(chat.id, chat.name)}
        onFocus={() => ctx.onFocusChat(chat)}
        onEditDucky={() => ctx.onEditDucky(chat)}
        isNew={ctx.newlyCreatedIds.has(`chat:${chat.id}`)}
        nestToggle={
          children.length > 0
            ? { expanded, onToggle: toggleNest, count: children.length }
            : undefined
        }
      />
      {children.length > 0 ? (
        <SidebarTreeChildren>
          {children.map((child) => (
            <ChatNode key={child.id} chat={child} ctx={ctx} depth={depth + 1} />
          ))}
        </SidebarTreeChildren>
      ) : null}
    </div>
  );
}

function FolderHeader({
  folder,
  isEditing,
  isSelectedParent,
  isActive,
  isFocused = false,
  hasRunningInside,
  editing,
  setEditing,
  editInputRef,
  onToggle,
  onSelectFolder,
  onModSelect,
  onCommitRename,
  onCancelRename,
  onRename,
  onDelete,
  onCreateDucky,
  onCreateGroup,
}: {
  folder: FolderItem;
  isEditing: boolean;
  isSelectedParent: boolean;
  isActive: boolean;
  isFocused?: boolean;
  hasRunningInside: boolean;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  onToggle: () => void;
  onSelectFolder: () => void;
  onModSelect?: (mods: SelectMods) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onRename: () => void;
  onDelete: () => void;
  onCreateDucky: () => void;
  onCreateGroup?: () => void;
}) {
  const id = dragId("folder", folder.id);
  const isProjectWrap = isProjectFolderId(folder.id);
  const isGlobalAgents = isGlobalProjectFolderId(folder.id);
  const dragDisabled = isProjectWrap;
  const rowSelection = useContext(RowSelectionContext);
  const deleteLabel = useRowDeleteCount(id) > 1 ? "Delete ALL" : "Delete";
  const { menu, open, close } = useContextMenuState<void>();
  const label = isGlobalAgents ? GLOBAL_AGENTS_LABEL : folder.name;

  const hoverPlacement = useContext(ChatTreeHoverPlacementContext);

  const handleChevronToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggle();
  };

  return (
    <FolderTabHoverCard folder={folder} placement={hoverPlacement}>
      <SidebarTreeRow
        leading={
          <span className="sidebar-folder-leading">
            <button
              type="button"
              className="chevron-icon sidebar-chat-nest-toggle"
              title={folder.expanded ? "Collapse" : "Expand"}
              aria-expanded={folder.expanded}
              onClick={handleChevronToggle}
            >
              <Icons.ChevronDown />
            </button>
            {hasRunningInside ? (
              <span className="sidebar-agent-spinner" title="Ducky working inside" aria-label="Ducky working inside" />
            ) : folder.groupHubId ? (
              <span className="sidebar-tree-row-icon sidebar-tree-row-icon--group" title="Group">
                <Icons.Users />
              </span>
            ) : (
              <span className="sidebar-tree-row-icon sidebar-tree-row-icon--folder" title="Folder" aria-hidden>
                <Icons.Folder />
              </span>
            )}
          </span>
        }
        label={label}
        isEditing={isEditing}
        renameInput={
          <input {...renameInputProps(editing, setEditing, editInputRef, onCommitRename, onCancelRename)} />
        }
        actions={
          isProjectWrap ? (
            <button
              type="button"
              className="sidebar-action-btn sidebar-project-add"
              title={isGlobalAgents ? "Add a ducky with no project" : "Add a ducky"}
              onClick={(e) => {
                e.stopPropagation();
                onCreateDucky();
              }}
            >
              <Icons.Plus />
            </button>
          ) : (
            <SidebarHoverActions onRename={onRename} onDelete={onDelete} deleteTitle={deleteLabel} />
          )
        }
        contextMenu={
          menu ? (
            <ContextMenu
              x={menu.x}
              y={menu.y}
              onClose={close}
              items={
                isProjectWrap
                  ? duckyTreeCreateItems(onCreateDucky, onCreateGroup)
                  : [
                      ...duckyTreeCreateItems(onCreateDucky, onCreateGroup),
                      contextMenuSeparator("folder-sep"),
                      { id: "rename", label: "Rename", onClick: onRename },
                      { id: "delete", label: deleteLabel, danger: true, onClick: onDelete },
                    ]
              }
            />
          ) : null
        }
        isActive={isActive}
        isParentSelected={isSelectedParent && !isActive}
        isFocused={isFocused}
        dataAttr="data-sidebar-id"
        dataId={id}
        draggable={!dragDisabled}
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("button")) return;
          if (e.button !== 0) return;
          if (isEditing) return;
          if (onModSelect && (e.ctrlKey || e.metaKey || e.shiftKey)) {
            onModSelect({ ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
            return;
          }
          onSelectFolder();
        }}
        onContextMenu={(e) => {
          e.preventDefault();
          rowSelection.onContextSelect(id);
          open(e, undefined);
        }}
      />
    </FolderTabHoverCard>
  );
}

const FolderGroup = memo(function FolderGroupImpl({
  folder,
  activeChats,
  runningChatIds,
  completionAlertChatIds,
  newlyCreatedIds,
  selectedChatFolderId,
  selectedIds,
  focusId,
  editing,
  setEditing,
  editInputRef,
  onToggle,
  onSelectChatFolder,
  onModSelectFolder,
  onSelectChat,
  onSelectChatPersistent,
  onModSelectChat,
  onRenameFolder,
  onDeleteFolder,
  onRenameChat,
  onDeleteChat,
  onFocusChat,
  onEditDucky,
  onCommitRename,
  onCancelRename,
  onCreateDuckyIn,
  onCreateGroupIn,
  filterQuery = "",
  visibleChatIds,
  visibleFolderIds,
  childrenByParent,
  childIds,
}: {
  folder: FolderItem;
  activeChats: string[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  newlyCreatedIds: Set<string>;
  selectedChatFolderId: string | null;
  selectedIds: ReadonlySet<string>;
  focusId: string | null;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  onToggle: (folderId: string) => void;
  onSelectChatFolder: (folderId: string) => void;
  onModSelectFolder: (folderId: string, mods: SelectMods) => void;
  onSelectChat: (chat: { id: string; name: string }) => void;
  onSelectChatPersistent?: (chat: { id: string; name: string }) => void;
  onModSelectChat: (chat: { id: string; name: string }, mods: SelectMods) => void;
  onRenameFolder: (id: string, name: string) => void;
  onDeleteFolder: (id: string, name: string) => void;
  onRenameChat: (id: string, name: string) => void;
  onDeleteChat: (id: string, name: string) => void;
  onFocusChat: (chat: { id: string; name: string }) => void;
  onEditDucky: (chat: { id: string; name: string; duckyStyle?: string; duckyPersonality?: string }) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onCreateDuckyIn: (folderId: string) => void;
  onCreateGroupIn: (folderId: string) => void;
  filterQuery?: string;
  visibleChatIds: Set<string>;
  visibleFolderIds: Set<string>;
  childrenByParent: Map<string, FolderItem["chats"]>;
  childIds: Set<string>;
}) {
  const filtering = Boolean(filterQuery.trim());
  const expanded = filtering || folder.expanded;
  const showAllChildren =
    filtering && visibleFolderIds.has(folder.id) && duckyNameMatches(filterQuery, folder.name);
  const childFolders = showAllChildren
    ? folder.children
    : folder.children.filter((child) => shouldShowFolder(child, filterQuery, visibleChatIds, visibleFolderIds));
  const childChatsUnfiltered = showAllChildren
    ? folder.chats
    : folder.chats.filter((chat) => shouldShowChat(chat, filterQuery, visibleChatIds));
  // Sub-agents render nested under their spawning chat, so drop them from the
  // flat folder listing (unless actively searching, where flat is clearer).
  const childChats = filtering ? childChatsUnfiltered : childChatsUnfiltered.filter((chat) => !childIds.has(chat.id));
  const folderEditing = editing?.kind === "folder" && editing.id === folder.id;
  const isNewFolder = newlyCreatedIds.has(`folder:${folder.id}`);
  const isSelectedParent = folder.id === selectedChatFolderId;
  const folderRowId = dragId("folder", folder.id);
  const hubId = (folder.groupHubId || "").trim();
  // Groups hide their hub chat from the tree — highlight the folder row when that hub is active.
  const isActive =
    (hubId ? activeChats.includes(hubId) : isSelectedParent) || selectedIds.has(folderRowId);
  // Show activity on the folder row when collapsed so nested work isn't invisible.
  const hasRunningInside =
    !expanded && anyRunningUnderFolder(folder, runningChatIds, childrenByParent);

  const projectEmpty =
    isProjectFolderId(folder.id) && !filtering && childChats.length === 0 && childFolders.length === 0;

  return (
    <div
      className={`sidebar-tree-branch ${!expanded ? "sidebar-tree-branch-collapsed" : ""} ${isNewFolder ? "sidebar-item-enter" : ""}`}
      data-tree-node={folderRowId}
    >
      <FolderHeader
        folder={folder}
        isEditing={folderEditing}
        isSelectedParent={isSelectedParent}
        isActive={isActive}
        isFocused={focusId === folderRowId}
        hasRunningInside={hasRunningInside}
        editing={editing}
        setEditing={setEditing}
        editInputRef={editInputRef}
        onToggle={() => onToggle(folder.id)}
        onSelectFolder={() => onSelectChatFolder(folder.id)}
        onModSelect={(mods) => onModSelectFolder(folder.id, mods)}
        onCommitRename={onCommitRename}
        onCancelRename={onCancelRename}
        onRename={() => onRenameFolder(folder.id, folder.name)}
        onDelete={() => onDeleteFolder(folder.id, folder.name)}
        onCreateDucky={() => onCreateDuckyIn(folder.id)}
        onCreateGroup={() => onCreateGroupIn(folder.id)}
      />

      <SidebarTreeChildren>
        {projectEmpty ? (
          <button type="button" className="sidebar-project-empty" onClick={() => onCreateDuckyIn(folder.id)}>
            No duckies — add one
          </button>
        ) : null}
          {childChats.map((chat) =>
            filtering ? (
            <ChatRow
              key={chat.id}
              chat={chat}
              isActive={activeChats.includes(chat.id) || selectedIds.has(dragId("chat", chat.id))}
              isFocused={focusId === dragId("chat", chat.id)}
              isRunning={runningChatIds.has(chat.id)}
              hasCompletionAlert={completionAlertChatIds?.has(chat.id) ?? false}
              isEditing={editing?.kind === "chat" && editing.id === chat.id}
              editing={editing}
              setEditing={setEditing}
              editInputRef={editInputRef}
              onSelect={() => onSelectChat(chat)}
              onSelectPersistent={() => (onSelectChatPersistent ?? onSelectChat)(chat)}
              onModSelect={(mods) => onModSelectChat(chat, mods)}
              onCommitRename={onCommitRename}
              onCancelRename={onCancelRename}
              onRename={() => onRenameChat(chat.id, chat.name)}
              onDelete={() => onDeleteChat(chat.id, chat.name)}
              onFocus={() => onFocusChat(chat)}
              onEditDucky={() => onEditDucky(chat)}
              isNew={newlyCreatedIds.has(`chat:${chat.id}`)}
            />
            ) : (
              <ChatNode
                key={chat.id}
                chat={chat}
                ctx={{
                  activeChats,
                  runningChatIds,
                  completionAlertChatIds,
                  newlyCreatedIds,
                  editing,
                  setEditing,
                  editInputRef,
                  childrenByParent,
                  selectedIds,
                  focusId,
                  onSelectChat,
                  onSelectChatPersistent,
                  onModSelectChat,
                  onRenameChat,
                  onDeleteChat,
                  onFocusChat,
                  onEditDucky,
                  onCommitRename,
                  onCancelRename,
                }}
              />
            ),
          )}
          {childFolders.map((child) => (
            <FolderGroup
              key={child.id}
              folder={child}
              activeChats={activeChats}
              runningChatIds={runningChatIds}
              completionAlertChatIds={completionAlertChatIds}
              newlyCreatedIds={newlyCreatedIds}
              selectedChatFolderId={selectedChatFolderId}
              selectedIds={selectedIds}
              focusId={focusId}
              editing={editing}
              setEditing={setEditing}
              editInputRef={editInputRef}
              onToggle={onToggle}
              onSelectChatFolder={onSelectChatFolder}
              onModSelectFolder={onModSelectFolder}
              onSelectChat={onSelectChat}
              onSelectChatPersistent={onSelectChatPersistent}
              onModSelectChat={onModSelectChat}
              onRenameFolder={onRenameFolder}
              onDeleteFolder={onDeleteFolder}
              onRenameChat={onRenameChat}
              onDeleteChat={onDeleteChat}
              onFocusChat={onFocusChat}
              onEditDucky={onEditDucky}
              onCommitRename={onCommitRename}
              onCancelRename={onCancelRename}
              onCreateDuckyIn={onCreateDuckyIn}
              onCreateGroupIn={onCreateGroupIn}
              filterQuery={filterQuery}
              visibleChatIds={visibleChatIds}
              visibleFolderIds={visibleFolderIds}
              childrenByParent={childrenByParent}
              childIds={childIds}
            />
          ))}
      </SidebarTreeChildren>
    </div>
  );
});

interface SidebarFolderTreeProps {
  folders: FolderItem[];
  setFolders: React.Dispatch<React.SetStateAction<FolderItem[]>>;
  rootChats: FolderItem["chats"];
  setRootChats: React.Dispatch<React.SetStateAction<FolderItem["chats"]>>;
  archiveChats: FolderItem["chats"];
  load: () => Promise<void>;
  activeChats: string[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  onChatSelect: (chat: { id: string; name: string }) => void;
  /** Double-click a chat: pin it (permanent tab) instead of the reusable preview tab. */
  onChatSelectPersistent?: (chat: { id: string; name: string }) => void;
  newlyCreatedIds: Set<string>;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onRenameFolder: (id: string, name: string) => void;
  onDeleteFolder: (id: string, name: string) => void;
  onRenameChat: (id: string, name: string) => void;
  onDeleteChat: (id: string, name: string) => void;
  /** Delete every row in a multi-selection at once. Resolves true once they're gone. */
  onDeleteSelection?: (targets: DuckyDeleteTarget[]) => Promise<boolean>;
  onFocusChat: (chat: { id: string; name: string }) => void;
  onEditDucky: (chat: { id: string; name: string; duckyStyle?: string; duckyPersonality?: string }) => void;
  selectedChatFolderId: string | null;
  onSelectChatFolder: (folderId: string) => void;
  onCreateDucky: () => void | Promise<void>;
  /** New group inside this folder; `projectSlug` when that folder is not on the open island. */
  onCreateGroup: (folderId?: string, projectSlug?: string) => void | Promise<void>;
  filterQuery?: string;
  /** Chat dropped onto the editor area — open at the VS Code-style zone. */
  onOpenChatInEditor?: (
    chat: { id: string; name: string },
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  /** Chat dropped outside the window — tear off into a focus window there. */
  onDetachChatAt?: (chat: { id: string; name: string }, at: { screenX: number; screenY: number }) => void;
  /** Which dock rail hosts this tree — hover cards open away from the rail. */
  dockSide?: DockSide;
  /** Multi-select size for the Duckies header badge (2…9 / 9+). */
  onSelectionCountChange?: (count: number) => void;
  /** Dense rows: hide chat meta line and shrink duck/group height. */
  compact?: boolean;
  /** Active island slug — duckies can be dragged onto another project or Global Agents. */
  currentProjectSlug?: string;
  /** New ducky in a project (and folder): Global Agents, another island, or this one. */
  onCreateInProject?: (projectSlug: string, folderId?: string) => void;
  /** Show the Global Agents folder (duckies with no island) at the top. */
  showGlobalAgents?: boolean;
}

export function SidebarFolderTree({
  folders,
  setFolders,
  rootChats,
  setRootChats,
  archiveChats,
  load,
  activeChats,
  runningChatIds,
  completionAlertChatIds,
  onChatSelect,
  onChatSelectPersistent,
  newlyCreatedIds,
  editing,
  setEditing,
  editInputRef,
  onCommitRename,
  onCancelRename,
  onRenameFolder,
  onDeleteFolder,
  onRenameChat,
  onDeleteChat,
  onDeleteSelection,
  onFocusChat,
  onEditDucky,
  selectedChatFolderId,
  onSelectChatFolder,
  onCreateDucky,
  onCreateGroup,
  filterQuery = "",
  onOpenChatInEditor,
  onDetachChatAt,
  dockSide = "left",
  onSelectionCountChange,
  compact = false,
  currentProjectSlug = "",
  onCreateInProject,
  showGlobalAgents = false,
}: SidebarFolderTreeProps) {
  const hoverPlacement: EditorTabHoverCardPlacement = dockSide === "right" ? "left" : "right";
  const [selection, setSelection] = useState<ExplorerSelection>(emptySelection);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  useEffect(() => {
    onSelectionCountChange?.(selection.selected.size);
  }, [selection.selected.size, onSelectionCountChange]);
  const filtering = Boolean(filterQuery.trim());
  const { chatIds: visibleChatIds, folderIds: visibleFolderIds } = useMemo(
    () => (filtering ? collectVisibleDuckyIds(filterQuery, folders, rootChats) : { chatIds: new Set<string>(), folderIds: new Set<string>() }),
    [filterQuery, filtering, folders, rootChats],
  );

  const filteredRootChats = useMemo(
    () => (filtering ? rootChats.filter((chat) => shouldShowChat(chat, filterQuery, visibleChatIds)) : rootChats),
    [filtering, rootChats, filterQuery, visibleChatIds],
  );

  const filteredFolders = useMemo(() => {
    const shown = duckiesFoldersForDisplay(folders);
    return filtering
      ? shown.filter((folder) => shouldShowFolder(folder, filterQuery, visibleChatIds, visibleFolderIds))
      : shown;
  }, [filtering, folders, filterQuery, visibleChatIds, visibleFolderIds]);

  // Global Agents: duckies with no island, shown on every island as a real folder
  // (chats, groups with their members, sub-folders). With no island open it holds them all.
  const globalFolder = useMemo(() => globalAgentsFolder(folders), [folders]);
  const currentIsGlobal = currentProjectSlug === GLOBAL_PROJECT_SLUG;
  const globalVisible = Boolean(
    globalFolder &&
      (showGlobalAgents || currentIsGlobal) &&
      (!filtering || shouldShowFolder(globalFolder, filterQuery, visibleChatIds, visibleFolderIds)),
  );

  // Map each spawning chat → the sub-agents it spawned, so the sidebar can nest
  // children under their parent regardless of which folder they live in. Only
  // links whose parent exists count (orphans fall back to top-level).
  const { childrenByParent, childIds } = useMemo(() => {
    const all: FolderItem["chats"] = [...rootChats];
    const walk = (items: FolderItem[]) => {
      for (const f of items) {
        all.push(...f.chats);
        walk(f.children);
      }
    };
    walk(folders);
    const ids = new Set(all.map((c) => c.id));
    const byId = new Map(all.map((c) => [c.id, c]));
    const byParent = new Map<string, FolderItem["chats"]>();
    const kids = new Set<string>();
    for (const c of all) {
      const pid = c.parentConvId;
      if (!pid || pid === c.id || !ids.has(pid)) continue;
      // Group members belong in the group folder, not tucked under the hub row.
      if (byId.get(pid)?.isGroup) continue;
      const bucket = byParent.get(pid) ?? [];
      bucket.push(c);
      byParent.set(pid, bucket);
      kids.add(c.id);
    }
    return { childrenByParent: byParent, childIds: kids };
  }, [folders, rootChats]);

  const rootChatsToRender = useMemo(
    () => (filtering ? filteredRootChats : filteredRootChats.filter((chat) => !childIds.has(chat.id))),
    [filtering, filteredRootChats, childIds],
  );

  useEffect(() => {
    if (!filtering || visibleFolderIds.size === 0) return;
    setFolders((prev) => {
      const needsExpand = [...visibleFolderIds].some((id) => {
        const folder = findFolderById(prev, id);
        return folder && !folder.expanded;
      });
      if (!needsExpand) return prev;
      return expandFoldersById(prev, visibleFolderIds);
    });
  }, [filtering, filterQuery, visibleFolderIds, setFolders]);
  const { menu: treeMenu, open: openTreeMenu, close: closeTreeMenu } = useContextMenuState<void>();

  const createDuckyIn = useCallback(
    (folderId: string) => {
      const target = duckiesCreateTarget(folderId, currentProjectSlug, folders);
      onSelectChatFolder(target.projectSlug ? "" : target.folderId);
      // The folder (and project) go along explicitly: the selection set above is not
      // visible to the create flow until the next render.
      if (onCreateInProject) {
        onCreateInProject(target.projectSlug ?? currentProjectSlug, target.folderId);
        return;
      }
      void onCreateDucky();
    },
    [currentProjectSlug, folders, onCreateDucky, onCreateInProject, onSelectChatFolder],
  );

  const createGroupIn = useCallback(
    (folderId: string) => {
      const target = duckiesCreateTarget(folderId, currentProjectSlug, folders);
      if (!target.projectSlug) onSelectChatFolder(target.folderId);
      void onCreateGroup(target.folderId, target.projectSlug);
    },
    [currentProjectSlug, folders, onCreateGroup, onSelectChatFolder],
  );

  const rootTreeMenuItems = useMemo(
    () =>
      duckyTreeCreateItems(
        () => void onCreateDucky(),
        () => void onCreateGroup(""),
      ),
    [onCreateDucky, onCreateGroup],
  );

  const toggleFolder = useCallback(
    (folderId: string) => {
      const toggleInTree = (items: FolderItem[]): FolderItem[] =>
        items.map((f) => ({
          ...f,
          expanded: f.id === folderId ? !f.expanded : f.expanded,
          children: toggleInTree(f.children),
        }));
      setFolders((prev) => toggleInTree(prev));
    },
    [setFolders],
  );

  // Flat order of rendered rows (respects collapsed folders) for shift-range selection.
  const visibleOrder = useCallback((): string[] => {
    return [...document.querySelectorAll<HTMLElement>("[data-sidebar-id]")]
      .map((n) => n.getAttribute("data-sidebar-id") || "")
      .filter(Boolean);
  }, []);

  const selectChatOnly = useCallback(
    (chat: { id: string; name: string }) => {
      setSelection(selectOnly(dragId("chat", chat.id)));
      onChatSelect(chat);
    },
    [onChatSelect],
  );

  const selectChatPersistentOnly = useCallback(
    (chat: { id: string; name: string }) => {
      setSelection(selectOnly(dragId("chat", chat.id)));
      (onChatSelectPersistent ?? onChatSelect)(chat);
    },
    [onChatSelect, onChatSelectPersistent],
  );

  const modSelectChat = useCallback(
    (chat: { id: string; name: string }, mods: SelectMods) => {
      const id = dragId("chat", chat.id);
      setSelection((prev) =>
        mods.shift ? rangeSelection(prev, id, visibleOrder()) : toggleSelection(prev, id),
      );
    },
    [visibleOrder],
  );

  const selectFolderOnly = useCallback(
    (folderId: string) => {
      setSelection(folderId ? selectOnly(dragId("folder", folderId)) : emptySelection());
      onSelectChatFolder(folderId);
    },
    [onSelectChatFolder],
  );

  const modSelectFolder = useCallback(
    (folderId: string, mods: SelectMods) => {
      const id = dragId("folder", folderId);
      setSelection((prev) =>
        mods.shift ? rangeSelection(prev, id, visibleOrder()) : toggleSelection(prev, id),
      );
    },
    [visibleOrder],
  );

  const rowNameById = useMemo(() => {
    const names = new Map<string, string>();
    const addChats = (chats: FolderItem["chats"]) => {
      for (const chat of chats) names.set(dragId("chat", chat.id), chat.name);
    };
    const walk = (items: FolderItem[]) => {
      for (const folder of items) {
        names.set(dragId("folder", folder.id), isGlobalProjectFolderId(folder.id) ? GLOBAL_AGENTS_LABEL : folder.name);
        addChats(folder.chats);
        walk(folder.children);
      }
    };
    addChats(rootChats);
    addChats(archiveChats);
    walk(folders);
    return names;
  }, [folders, rootChats, archiveChats]);
  const rowNameByIdRef = useRef(rowNameById);
  rowNameByIdRef.current = rowNameById;

  const contextSelectRow = useCallback(
    (rowId: string) => {
      // Already part of the selection: leave it alone so the menu can act on all of it.
      if (selectionRef.current.selected.has(rowId)) return;
      setSelection(selectOnly(rowId));
      const parsed = parseDragId(rowId);
      if (parsed?.kind === "folder") onSelectChatFolder(parsed.id);
    },
    [onSelectChatFolder],
  );

  const rowSelection = useMemo<RowSelectionCtx>(
    () => ({ selected: selection.selected, onContextSelect: contextSelectRow }),
    [selection.selected, contextSelectRow],
  );

  /** Route a row delete through the selection when that row is part of one. */
  const deleteRow = useCallback(
    (kind: "folder" | "chat", id: string, name: string) => {
      const rowId = dragId(kind, id);
      const selected = selectionRef.current.selected;
      if (!onDeleteSelection || selected.size < 2 || !selected.has(rowId)) {
        if (kind === "folder") onDeleteFolder(id, name);
        else onDeleteChat(id, name);
        return;
      }
      const targets: DuckyDeleteTarget[] = [];
      for (const selectedId of selected) {
        const parsed = parseDragId(selectedId);
        if (!parsed) continue;
        targets.push({ ...parsed, name: rowNameById.get(selectedId) ?? "" });
      }
      void onDeleteSelection(targets).then((deleted) => {
        if (deleted) setSelection(emptySelection());
      });
    },
    [onDeleteChat, onDeleteFolder, onDeleteSelection, rowNameById],
  );

  const deleteFolderRow = useCallback(
    (id: string, name: string) => deleteRow("folder", id, name),
    [deleteRow],
  );

  const deleteChatRow = useCallback(
    (id: string, name: string) => deleteRow("chat", id, name),
    [deleteRow],
  );

  // ── Drag and drop: the shared tree engine (tree-dnd) ─────────────────────────
  const treeData = useMemo<DuckiesTreeData>(() => ({ folders, rootChats }), [folders, rootChats]);
  const treeDataRef = useRef(treeData);
  treeDataRef.current = treeData;
  const treeIndex = useMemo(() => buildDuckiesIndex(treeData, currentProjectSlug), [treeData, currentProjectSlug]);
  const treeIndexRef = useRef(treeIndex);
  treeIndexRef.current = treeIndex;
  const dropPolicy = useMemo(() => duckiesDropPolicy(treeIndex, filtering), [treeIndex, filtering]);
  const dropPolicyRef = useRef(dropPolicy);
  dropPolicyRef.current = dropPolicy;
  const currentSlugRef = useRef(currentProjectSlug);
  currentSlugRef.current = currentProjectSlug;
  const undoHistory = useUndoHistoryOptional();
  const loadRef = useRef(load);
  loadRef.current = load;

  /** Show the move at once, then save it. Reloads racing the save are held back, so the
   *  tree never jumps back to where the row was. */
  const applyPlan = useCallback(
    async (plan: DuckiesMovePlan) => {
      treeDataRef.current = plan.data;
      setFolders(plan.data.folders);
      setRootChats(plan.data.rootChats);
      const api = getApi();
      if (!api) return;
      const release = duckiesLayoutHold.hold();
      try {
        for (const move of plan.projectMoves) {
          if (!move.convIds.length && !move.folderIds.length) continue;
          await api.move_chats_to_project?.(move.convIds, move.folderIds, move.slug, move.folderId);
        }
        for (const slug of plan.layoutSlugs) {
          const patch = duckiesLayoutFor(plan.data, slug, currentSlugRef.current);
          if (patch) await api.apply_sidebar_layout(patch);
        }
      } catch {
        // Refused (the tree was stale): reload what is saved.
        void loadRef.current();
      } finally {
        release();
      }
    },
    [setFolders, setRootChats],
  );

  const chatForNode = useCallback((nodeId: string): { id: string; name: string } | null => {
    const chat = treeIndexRef.current.chats.get(nodeId);
    return chat ? { id: chat.id, name: chat.name } : null;
  }, []);

  const dnd = useTreeDnd({
    getModel: () => treeIndexRef.current.model,
    getPolicy: () => dropPolicyRef.current,
    dragSources: (id) => {
      const selected = selectionRef.current.selected;
      if (selected.has(id) && selected.size > 1) return [...selected];
      if (!selected.has(id)) setSelection(selectOnly(id));
      return [id];
    },
    canDrag: (id) => !isProjectNode(id) && (treeIndexRef.current.chats.has(id) || treeIndexRef.current.folders.has(id)),
    dragData: () => "duckies",
    labelFor: (id) => rowNameByIdRef.current.get(id) ?? "",
    expand: (id) => {
      const parsed = parseDragId(id);
      if (parsed?.kind === "folder") setFolders((prev) => expandFoldersById(prev, new Set([parsed.id])));
    },
    dropOutside: {
      accepts: (sources) => sources.length === 1 && treeIndexRef.current.chats.has(sources[0]),
      onEditor: (sources, at) => {
        const chat = chatForNode(sources[0]);
        if (!chat) return;
        if (onOpenChatInEditor) onOpenChatInEditor(chat, { groupId: at.groupId, zone: at.zone });
        else onChatSelect(chat);
      },
      onTearOff: (sources, at) => {
        const chat = chatForNode(sources[0]);
        if (chat) onDetachChatAt?.(chat, at);
      },
    },
    onDrop: ({ sources, target }) => {
      const index = treeIndexRef.current;
      const places = duckiesPlaces(index, sources);
      void applyPlan(planDuckiesDrop(index, treeDataRef.current, sources, target, dropPolicyRef.current));
      keepTreeFocus("chats");
      undoHistory?.push("chats", {
        label: sources.length > 1 ? `Move ${sources.length} items` : "Move",
        undo: async () => {
          const now = buildDuckiesIndex(treeDataRef.current, currentSlugRef.current);
          await applyPlan(planDuckiesRestore(now, treeDataRef.current, places));
        },
        redo: async () => {
          const now = buildDuckiesIndex(treeDataRef.current, currentSlugRef.current);
          const moving = topLevelSources(now.model, sources);
          if (!moving.length || !now.model.parent.has(target.parentId)) return;
          await applyPlan(planDuckiesDrop(now, treeDataRef.current, moving, target, duckiesDropPolicy(now)));
        },
      });
    },
  });

  useEffect(() => {
    const primaryChatId = activeChats[0];
    if (!primaryChatId) return;
    const frame = window.requestAnimationFrame(() => {
      const el = document.querySelector(`[data-sidebar-id="${dragId("chat", primaryChatId)}"]`);
      el?.scrollIntoView({ block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeChats]);

  if (filtering && filteredRootChats.length === 0 && filteredFolders.length === 0 && !globalVisible) {
    return (
      <>
        {treeMenu ? (
          <ContextMenu x={treeMenu.x} y={treeMenu.y} onClose={closeTreeMenu} items={rootTreeMenuItems} />
        ) : null}
        <div className="ui-status-sidebar-muted file-tree-empty">
          No duckies match &ldquo;{filterQuery.trim()}&rdquo;
        </div>
      </>
    );
  }

  const groupProps = {
    activeChats,
    runningChatIds,
    completionAlertChatIds,
    newlyCreatedIds,
    selectedChatFolderId,
    selectedIds: selection.selected,
    focusId: selection.focus,
    editing,
    setEditing,
    editInputRef,
    onToggle: toggleFolder,
    onSelectChatFolder: selectFolderOnly,
    onModSelectFolder: modSelectFolder,
    onSelectChat: selectChatOnly,
    onSelectChatPersistent: selectChatPersistentOnly,
    onModSelectChat: modSelectChat,
    onRenameFolder,
    onDeleteFolder: deleteFolderRow,
    onRenameChat,
    onDeleteChat: deleteChatRow,
    onFocusChat,
    onEditDucky,
    onCommitRename,
    onCancelRename,
    onCreateDuckyIn: createDuckyIn,
    onCreateGroupIn: createGroupIn,
    filterQuery,
    visibleChatIds,
    visibleFolderIds,
    childrenByParent,
    childIds,
  };

  const projectEmpty = filteredRootChats.length === 0 && filteredFolders.length === 0;

  return (
    <ChatTreeHoverPlacementContext.Provider value={hoverPlacement}>
    <RowSelectionContext.Provider value={rowSelection}>
    <>
      {treeMenu ? (
        <ContextMenu x={treeMenu.x} y={treeMenu.y} onClose={closeTreeMenu} items={rootTreeMenuItems} />
      ) : null}
      <div
        {...dnd.rootProps}
        className={["ducky-tree", compact ? "is-compact" : ""].filter(Boolean).join(" ")}
        data-undo-scope="chats"
        tabIndex={-1}
        onContextMenu={(e) => {
          if ((e.target as HTMLElement).closest("[data-sidebar-id]")) return;
          selectFolderOnly("");
          openTreeMenu(e, undefined);
        }}
      >
        <DuckiesCompactContext.Provider value={compact}>
        <DuckiesCurrentProjectContext.Provider value={currentProjectSlug}>
          {globalVisible && globalFolder ? (
            <div className="sidebar-global-agents">
              <FolderGroup folder={globalFolder} {...groupProps} />
            </div>
          ) : null}
          {rootChatsToRender.map((chat) =>
            filtering ? (
            <ChatRow
              key={chat.id}
              chat={chat}
              isActive={activeChats.includes(chat.id) || selection.selected.has(dragId("chat", chat.id))}
              isFocused={selection.focus === dragId("chat", chat.id)}
              isRunning={runningChatIds.has(chat.id)}
              hasCompletionAlert={completionAlertChatIds?.has(chat.id) ?? false}
              isEditing={editing?.kind === "chat" && editing.id === chat.id}
              editing={editing}
              setEditing={setEditing}
              editInputRef={editInputRef}
              onSelect={() => selectChatOnly(chat)}
              onSelectPersistent={() => selectChatPersistentOnly(chat)}
              onModSelect={(mods) => modSelectChat(chat, mods)}
              onCommitRename={onCommitRename}
              onCancelRename={onCancelRename}
              onRename={() => onRenameChat(chat.id, chat.name)}
              onDelete={() => deleteChatRow(chat.id, chat.name)}
              onFocus={() => onFocusChat(chat)}
              onEditDucky={() => onEditDucky(chat)}
              isNew={newlyCreatedIds.has(`chat:${chat.id}`)}
            />
            ) : (
              <ChatNode
                key={chat.id}
                chat={chat}
                ctx={{
                  activeChats,
                  runningChatIds,
                  completionAlertChatIds,
                  newlyCreatedIds,
                  editing,
                  setEditing,
                  editInputRef,
                  childrenByParent,
                  selectedIds: selection.selected,
                  focusId: selection.focus,
                  onSelectChat: selectChatOnly,
                  onSelectChatPersistent: selectChatPersistentOnly,
                  onModSelectChat: modSelectChat,
                  onRenameChat,
                  onDeleteChat: deleteChatRow,
                  onFocusChat,
                  onEditDucky,
                  onCommitRename,
                  onCancelRename,
                }}
              />
            ),
          )}
          {filteredFolders.map((folder) => (
            <FolderGroup key={folder.id} folder={folder} {...groupProps} />
          ))}
          {projectEmpty && !(globalVisible && currentIsGlobal) ? (
            <div className="ui-status-sidebar-muted file-tree-empty">
              No duckies yet — right-click to create one.
            </div>
          ) : null}
        </DuckiesCurrentProjectContext.Provider>
        </DuckiesCompactContext.Provider>
      </div>
    </>
    </RowSelectionContext.Provider>
    </ChatTreeHoverPlacementContext.Provider>
  );
}

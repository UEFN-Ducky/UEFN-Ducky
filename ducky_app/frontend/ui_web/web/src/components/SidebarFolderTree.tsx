import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCenter,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
} from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useScopedClass } from "../utils/scopedCss";
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
  applySidebarDrag,
  appendArchiveChatsToLayout,
  dragId,
  expandFoldersById,
  findFolderById,
  flattenLayout,
  nestDropId,
  parseDragId,
  resolveDragOverId,
  type DropPosition,
} from "../utils/sidebarTree";
import {
  collectVisibleDuckyIds,
  duckyNameMatches,
  shouldShowChat,
  shouldShowFolder,
} from "../utils/duckyTreeFilter";
import { anyRunningUnderFolder } from "../utils/duckyTreeBusy";
import type { EditorDropZone } from "../types/panel";
import { classifySidebarDragOut, type SidebarDragPoint } from "../utils/sidebarDragOut";
import { formatRelativeTime } from "../utils/formatRelativeTime";
import {
  chatNestDefaultExpanded,
  loadChatNestExpanded,
  saveChatNestExpanded,
} from "../utils/chatNestExpand";
import { useSidebarDragPointerTracking } from "../hooks/useSidebarDragPointerTracking";
import { SidebarTreeChildren } from "./sidebar/SidebarTreeChildren";
import { SidebarTreeRow } from "./sidebar/SidebarTreeRow";
import { ChatTabHoverCard } from "./editor/ChatTabHoverCard";
import { FolderTabHoverCard } from "./editor/FolderTabHoverCard";
import {
  computeDropPosition,
  renameInputProps,
  SidebarHoverActions,
  SORTABLE_STATIC,
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
type DropHint = { overId: string; position: DropPosition };
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
  dropHint,
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
  dropHint: DropHint | null;
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
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({ id, ...SORTABLE_STATIC });
  const rowRef = useRef<HTMLDivElement | null>(null);
  const rowScopeClass = useScopedClass("dnd-row");
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

  const mergeRowRef = (node: HTMLDivElement | null) => {
    setNodeRef(node);
    rowRef.current = node;
  };

  const dropClass =
    dropHint?.overId === id
      ? dropHint.position === "before"
        ? "drop-before"
        : dropHint.position === "after"
          ? "drop-after"
          : ""
      : "";

  return (
    <ChatTabHoverCard
      chat={chat}
      isRunning={isRunning}
      hasCompletionAlert={hasCompletionAlert}
      disabled={isDragging}
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
              className={`sidebar-tree-row-icon sidebar-tree-row-icon--chat${hasCompletionAlert && !isRunning ? " chat-completion-alert" : ""}${chat.isLeader ? " sidebar-tree-row-icon--leader" : ""}`}
              title={chat.isLeader ? "Group leader" : undefined}
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
        isDragging={isDragging}
        isNew={isNew}
        dropClass={dropClass}
        rowScopeClass={rowScopeClass}
        dndTransform={null}
        dndTransition={undefined}
        mergeRowRef={mergeRowRef}
        dataAttr="data-sidebar-id"
        dataId={id}
        attributes={attributes}
        listeners={listeners}
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
  dropHint: DropHint | null;
  childrenByParent: Map<string, FolderItem["chats"]>;
  selectedIds: Set<string>;
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
    <div className={`sidebar-tree-branch ${children.length > 0 && !expanded ? "sidebar-tree-branch-collapsed" : ""}`}>
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
        dropHint={ctx.dropHint}
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
  dropHint,
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
  dropHint: DropHint | null;
}) {
  const id = dragId("folder", folder.id);
  const rowSelection = useContext(RowSelectionContext);
  const deleteLabel = useRowDeleteCount(id) > 1 ? "Delete ALL" : "Delete";
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({ id, ...SORTABLE_STATIC });
  const rowRef = useRef<HTMLDivElement | null>(null);
  const rowScopeClass = useScopedClass("dnd-row");
  const { menu, open, close } = useContextMenuState<void>();

  const mergeRowRef = (node: HTMLDivElement | null) => {
    setNodeRef(node);
    rowRef.current = node;
  };

  const dropClass =
    dropHint?.overId === id
      ? dropHint.position === "before"
        ? "drop-before"
        : dropHint.position === "after"
          ? "drop-after"
          : dropHint.position === "inside"
            ? "drop-inside"
            : ""
      : "";

  const hoverPlacement = useContext(ChatTreeHoverPlacementContext);

  const handleChevronToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggle();
  };

  return (
    <FolderTabHoverCard folder={folder} disabled={isDragging} placement={hoverPlacement}>
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
        label={folder.name}
        isEditing={isEditing}
        renameInput={
          <input {...renameInputProps(editing, setEditing, editInputRef, onCommitRename, onCancelRename)} />
        }
        actions={<SidebarHoverActions onRename={onRename} onDelete={onDelete} deleteTitle={deleteLabel} />}
        contextMenu={
          menu ? (
            <ContextMenu
              x={menu.x}
              y={menu.y}
              onClose={close}
              items={[
                ...duckyTreeCreateItems(onCreateDucky, onCreateGroup),
                contextMenuSeparator("folder-sep"),
                { id: "rename", label: "Rename", onClick: onRename },
                { id: "delete", label: deleteLabel, danger: true, onClick: onDelete },
              ]}
            />
          ) : null
        }
        isActive={isActive}
        isParentSelected={isSelectedParent && !isActive}
        isFocused={isFocused}
        isDragging={isDragging}
        dropClass={dropClass}
        rowScopeClass={rowScopeClass}
        dndTransform={null}
        dndTransition={undefined}
        mergeRowRef={mergeRowRef}
        dataAttr="data-sidebar-id"
        dataId={id}
        attributes={attributes}
        listeners={listeners}
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
  dropHint,
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
  selectedIds: Set<string>;
  focusId: string | null;
  editing: EditTarget | null;
  setEditing: React.Dispatch<React.SetStateAction<EditTarget | null>>;
  editInputRef: React.RefObject<HTMLInputElement>;
  dropHint: DropHint | null;
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

  const childFolderIds = childFolders.map((c) => dragId("folder", c.id));
  const chatIds = childChats.map((c) => dragId("chat", c.id));
  // Match root: duckies above folders inside every folder.
  const childSortableIds = [...chatIds, ...childFolderIds];

  const { setNodeRef: setNestRef } = useDroppable({ id: nestDropId(folder.id) });

  return (
    <div
      className={`sidebar-tree-branch ${!expanded ? "sidebar-tree-branch-collapsed" : ""} ${isNewFolder ? "sidebar-item-enter" : ""}`}
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
        dropHint={dropHint}
      />

      <SidebarTreeChildren nestRef={setNestRef}>
        <SortableContext items={childSortableIds} strategy={verticalListSortingStrategy}>
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
              dropHint={dropHint}
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
                  dropHint,
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
              dropHint={dropHint}
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
        </SortableContext>
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
  setArchiveChats: React.Dispatch<React.SetStateAction<FolderItem["chats"]>>;
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
  onCreateGroup: (folderId?: string) => void | Promise<void>;
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
}

export function SidebarFolderTree({
  folders,
  setFolders,
  rootChats,
  setRootChats,
  archiveChats,
  setArchiveChats,
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

  const filteredFolders = useMemo(
    () => (filtering ? folders.filter((folder) => shouldShowFolder(folder, filterQuery, visibleChatIds, visibleFolderIds)) : folders),
    [filtering, folders, filterQuery, visibleChatIds, visibleFolderIds],
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
    const byParent = new Map<string, FolderItem["chats"]>();
    const kids = new Set<string>();
    for (const c of all) {
      const pid = c.parentConvId;
      if (pid && pid !== c.id && ids.has(pid)) {
        const bucket = byParent.get(pid) ?? [];
        bucket.push(c);
        byParent.set(pid, bucket);
        kids.add(c.id);
      }
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
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [dropHint, setDropHint] = useState<DropHint | null>(null);
  const pointerYRef = useRef(0);
  const dragPointRef = useRef<SidebarDragPoint>({ clientX: 0, clientY: 0, screenX: 0, screenY: 0 });
  const { menu: treeMenu, open: openTreeMenu, close: closeTreeMenu } = useContextMenuState<void>();

  const createDuckyIn = useCallback(
    (folderId: string) => {
      onSelectChatFolder(folderId);
      void onCreateDucky();
    },
    [onCreateDucky, onSelectChatFolder],
  );

  const createGroupIn = useCallback(
    (folderId: string) => {
      onSelectChatFolder(folderId);
      // Pass the parent explicitly: the selection state set above isn't visible
      // to onCreateGroup's closure until the next render.
      void onCreateGroup(folderId);
    },
    [onCreateGroup, onSelectChatFolder],
  );

  const rootTreeMenuItems = useMemo(
    () =>
      duckyTreeCreateItems(
        () => void onCreateDucky(),
        () => void onCreateGroup(""),
      ),
    [onCreateDucky, onCreateGroup],
  );

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const rootChatIds = useMemo(() => rootChatsToRender.map((c) => dragId("chat", c.id)), [rootChatsToRender]);
  const rootFolderIds = useMemo(() => filteredFolders.map((f) => dragId("folder", f.id)), [filteredFolders]);
  const rootSortableIds = useMemo(() => [...rootChatIds, ...rootFolderIds], [rootChatIds, rootFolderIds]);
  const overIdRef = useRef<string | null>(null);
  const rootNestId = nestDropId("root");

  const updateDropHint = useCallback((overId: string | null) => {
    overIdRef.current = overId;
    if (!overId) {
      setDropHint(null);
      return;
    }
    const nestParsed = overId.startsWith("nest:") ? overId.slice(5) : null;
    if (nestParsed !== null) {
      if (nestParsed === "root") {
        setDropHint({ overId: rootNestId, position: "inside" });
        return;
      }
      setDropHint({ overId: dragId("folder", nestParsed), position: "inside" });
      return;
    }

    const parsed = parseDragId(overId);
    if (!parsed) {
      setDropHint(null);
      return;
    }

    const el = document.querySelector(`[data-sidebar-id="${overId}"]`);
    if (!el) {
      setDropHint(null);
      return;
    }
    const rect = el.getBoundingClientRect();
    const position = computeDropPosition(pointerYRef.current, rect, parsed.kind === "folder");
    setDropHint({ overId, position });
  }, [rootNestId]);

  const refreshDropHint = useCallback(() => {
    updateDropHint(overIdRef.current);
  }, [updateDropHint]);

  useSidebarDragPointerTracking(activeDragId, pointerYRef, dragPointRef, setDropHint, refreshDropHint);

  const collisionDetection = useCallback<CollisionDetection>((args) => {
    const within = pointerWithin(args);
    return within.length ? within : closestCenter(args);
  }, []);

  const toggleFolder = (folderId: string) => {
    const toggleInTree = (items: FolderItem[]): FolderItem[] =>
      items.map((f) => ({
        ...f,
        expanded: f.id === folderId ? !f.expanded : f.expanded,
        children: toggleInTree(f.children),
      }));
    setFolders((prev) => toggleInTree(prev));
  };

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
        names.set(dragId("folder", folder.id), folder.name);
        addChats(folder.chats);
        walk(folder.children);
      }
    };
    addChats(rootChats);
    addChats(archiveChats);
    walk(folders);
    return names;
  }, [folders, rootChats, archiveChats]);

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

  const persistLayout = async (
    nextFolders: FolderItem[],
    nextRootChats: FolderItem["chats"],
    nextArchiveChats: FolderItem["chats"],
  ) => {
    const api = getApi();
    if (!api?.apply_sidebar_layout) return;
    const patch = appendArchiveChatsToLayout(flattenLayout(nextFolders, nextRootChats), nextArchiveChats);
    try {
      await api.apply_sidebar_layout(patch);
    } catch {
      await load();
    }
  };

  const handleDragOver = (event: DragOverEvent) => {
    const overId = event.over ? String(event.over.id) : null;
    updateDropHint(overId);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveDragId(null);
    const hint = dropHint;
    setDropHint(null);
    overIdRef.current = null;

    // Drag left the sidebar: open instead of reorder. closestCenter keeps `over`
    // pointing at the nearest row even far outside the tree, so the pointer
    // position — not `over` — decides this.
    const dragOutZone = classifySidebarDragOut(dragPointRef.current);
    if (dragOutZone) {
      const parsed = parseDragId(String(active.id));
      if (parsed?.kind === "chat" && activeLabel) {
        const chat = { id: parsed.id, name: activeLabel };
        if (dragOutZone.kind === "editor") {
          onOpenChatInEditor?.(chat, { groupId: dragOutZone.groupId, zone: dragOutZone.zone });
        } else {
          onDetachChatAt?.(chat, { screenX: dragOutZone.screenX, screenY: dragOutZone.screenY });
        }
      }
      return;
    }

    if (!over && !hint) return;

    const activeId = String(active.id);
    let overId = hint?.overId ?? (over ? String(over.id) : null);
    if (!overId) return;
    if (hint) {
      overId = resolveDragOverId(folders, rootChats, hint.overId, hint.position, activeId);
    } else if (!overId.startsWith("nest:")) {
      const parsed = parseDragId(overId);
      if (parsed?.kind === "folder") {
        const el = document.querySelector(`[data-sidebar-id="${overId}"]`);
        if (el) {
          const rect = el.getBoundingClientRect();
          const pos = computeDropPosition(pointerYRef.current, rect, true);
          overId = resolveDragOverId(folders, rootChats, overId, pos, activeId);
        }
      }
    }

    const sel = selectionRef.current.selected;
    // If the dragged row is part of a multi-selection, move the whole set.
    const sources = sel.has(activeId) && sel.size > 1 ? [...sel] : [activeId];
    let nextFolders = folders;
    let nextRoot = rootChats;
    let nextArchive = archiveChats;
    let moved = false;
    for (const src of sources) {
      if (src === overId) continue;
      const next = applySidebarDrag(nextFolders, nextRoot, src, overId, nextArchive);
      if (!next) continue;
      nextFolders = next.folders;
      nextRoot = next.rootChats;
      nextArchive = next.archiveChats;
      moved = true;
    }
    if (!moved) return;
    setFolders(nextFolders);
    setRootChats(nextRoot);
    setArchiveChats(nextArchive);
    void persistLayout(nextFolders, nextRoot, nextArchive);
  };

  const activeLabel = useMemo(() => {
    if (!activeDragId) return "";
    const parsed = parseDragId(activeDragId);
    if (!parsed) return "";
    if (parsed.kind === "folder") {
      const findName = (items: FolderItem[]): string => {
        for (const f of items) {
          if (f.id === parsed.id) return f.name;
          const nested = findName(f.children);
          if (nested) return nested;
        }
        return "";
      };
      return findName(folders);
    }
    if (parsed.kind === "chat") {
      const rootChat = rootChats.find((c) => c.id === parsed.id);
      if (rootChat) return rootChat.name;
      const archived = archiveChats.find((c) => c.id === parsed.id);
      if (archived) return archived.name;
    }
    for (const f of folders) {
      const walk = (items: FolderItem[]): string => {
        for (const folder of items) {
          const chat = folder.chats.find((c) => c.id === parsed.id);
          if (chat) return chat.name;
          const nested = walk(folder.children);
          if (nested) return nested;
        }
        return "";
      };
      const name = walk([f]);
      if (name) return name;
    }
    return "";
  }, [activeDragId, folders, rootChats, archiveChats]);

  const { setNodeRef: setRootDropRef, isOver: rootIsOver } = useDroppable({ id: nestDropId("root") });

  useEffect(() => {
    const primaryChatId = activeChats[0];
    if (!primaryChatId) return;
    const frame = window.requestAnimationFrame(() => {
      const el = document.querySelector(`[data-sidebar-id="${dragId("chat", primaryChatId)}"]`);
      el?.scrollIntoView({ block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeChats]);

  if (filtering && filteredRootChats.length === 0 && filteredFolders.length === 0) {
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

  return (
    <ChatTreeHoverPlacementContext.Provider value={hoverPlacement}>
    <RowSelectionContext.Provider value={rowSelection}>
    <>
      {treeMenu ? (
        <ContextMenu x={treeMenu.x} y={treeMenu.y} onClose={closeTreeMenu} items={rootTreeMenuItems} />
      ) : null}
    <DndContext
      sensors={sensors}
      collisionDetection={collisionDetection}
      onDragStart={(e) => {
        const id = String(e.active.id);
        setActiveDragId(id);
        if (!selectionRef.current.selected.has(id)) {
          setSelection(selectOnly(id));
        }
        if (e.activatorEvent && "clientY" in e.activatorEvent) {
          const pe = e.activatorEvent as PointerEvent;
          pointerYRef.current = pe.clientY;
          dragPointRef.current = { clientX: pe.clientX, clientY: pe.clientY, screenX: pe.screenX, screenY: pe.screenY };
        }
      }}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
      onDragCancel={() => {
        setActiveDragId(null);
        setDropHint(null);
        overIdRef.current = null;
      }}
    >
      <div
        ref={setRootDropRef}
        className={[
          "ducky-tree",
          compact ? "is-compact" : "",
          rootIsOver || dropHint?.overId === rootNestId ? "sidebar-drop-root-active" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        onContextMenu={(e) => {
          if ((e.target as HTMLElement).closest("[data-sidebar-id]")) return;
          selectFolderOnly("");
          openTreeMenu(e, undefined);
        }}
      >
        <DuckiesCompactContext.Provider value={compact}>
        <SortableContext items={rootSortableIds} strategy={verticalListSortingStrategy}>
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
              dropHint={dropHint}
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
                  dropHint,
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
            <FolderGroup
              key={folder.id}
              folder={folder}
              activeChats={activeChats}
              runningChatIds={runningChatIds}
              completionAlertChatIds={completionAlertChatIds}
              newlyCreatedIds={newlyCreatedIds}
              selectedChatFolderId={selectedChatFolderId}
              selectedIds={selection.selected}
              focusId={selection.focus}
              editing={editing}
              setEditing={setEditing}
              editInputRef={editInputRef}
              dropHint={dropHint}
              onToggle={toggleFolder}
              onSelectChatFolder={selectFolderOnly}
              onModSelectFolder={modSelectFolder}
              onSelectChat={selectChatOnly}
              onSelectChatPersistent={selectChatPersistentOnly}
              onModSelectChat={modSelectChat}
              onRenameFolder={onRenameFolder}
              onDeleteFolder={deleteFolderRow}
              onRenameChat={onRenameChat}
              onDeleteChat={deleteChatRow}
              onFocusChat={onFocusChat}
              onEditDucky={onEditDucky}
              onCommitRename={onCommitRename}
              onCancelRename={onCancelRename}
              onCreateDuckyIn={createDuckyIn}
              onCreateGroupIn={createGroupIn}
              filterQuery={filterQuery}
              visibleChatIds={visibleChatIds}
              visibleFolderIds={visibleFolderIds}
              childrenByParent={childrenByParent}
              childIds={childIds}
            />
          ))}
        </SortableContext>
        {filteredRootChats.length === 0 && filteredFolders.length === 0 ? (
          <div className="ui-status-sidebar-muted file-tree-empty">
            No duckies yet — right-click to create one.
          </div>
        ) : null}
        </DuckiesCompactContext.Provider>
      </div>
      <DragOverlay>
        {activeDragId ? (
          <div className="sidebar-drag-overlay">
            <Icons.Duck />
            <span>
              {selection.selected.has(activeDragId) && selection.selected.size > 1
                ? `${selection.selected.size} items`
                : activeLabel}
            </span>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
    </>
    </RowSelectionContext.Provider>
    </ChatTreeHoverPlacementContext.Provider>
  );
}

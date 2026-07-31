import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  useMemo,
  forwardRef,
  type MouseEvent,
  type ReactNode,
  type Ref,
} from "react";
import { Icons } from "../icons/Icons";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { SidebarFolderTree, type DuckyDeleteTarget } from "./SidebarFolderTree";
import { SidebarFileTree, type SidebarFileTreeHandle } from "./SidebarFileTree";
import { ErrorBoundary } from "./ErrorBoundary";
import { SidebarSectionHeader } from "./sidebar/SidebarSectionHeader";
import { ContentSearchBar, type PanelSearchMode } from "./sidebar/ContentSearchBar";
import { SidebarTextSearchResults } from "./sidebar/SidebarTextSearchResults";
import { useSidebarTextSearch } from "../hooks/useSidebarTextSearch";
import { CtrlWheelZoomRoot } from "./CtrlWheelZoomRoot";
import { sidebarPanelZoomKey } from "../hooks/useCtrlWheelZoom";
import { SplitResizeHandle } from "./SplitResizeHandle";
import { VerseTemplatePicker } from "../verse-editor/components/VerseTemplatePicker";
import type { VerseTemplate } from "../verse-editor/templates/types";
import type { EditorDropZone, FolderItem, SidebarPanelTab } from "../types/panel";
import type { DockPanelId, DockSide } from "../workspace/workspaceDockStorage";
import { useWorkspaceDockOptional } from "../workspace/WorkspaceDockContext";
import { ARCHIVE_FOLDER_ID } from "../utils/archiveFolder";
import { targetRef } from "../ui-targets/registry";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import { useProjectFilesSettings } from "../contexts/ProjectFilesSettingsContext";
import { useCreateDucky } from "../hooks/useCreateDucky";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useSidebarPanelLayout } from "../hooks/useSidebarPanelLayout";
import { useSidebarPanelMode } from "../hooks/useSidebarPanelMode";
import { useSidebarWidth } from "../hooks/useSidebarWidth";
import { getApi } from "../hooks/usePanelApi";
import { SidebarStackedPanels } from "./sidebar/SidebarStackedPanels";
import { SidebarPanelTabs } from "./sidebar/SidebarPanelTabs";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import { insertIndexForTabDrop } from "../workspace/dockTabInsertIndex";
import { DuckyArchiveDropdown } from "./sidebar/DuckyArchiveDropdown";
import { ContextMenu, useContextMenuState } from "./ContextMenu";
import { formatSelectionBadge } from "../utils/fileTreeSelection";
import { numberedEntryName } from "../utils/numberedEntryName";
import {
  contextMenuSeparator,
  fileTreeCreateItems,
  duckyTreeCompactItem,
  duckyTreeCreateItems,
  showHiddenProjectFilesItem,
} from "../utils/sidebarContextMenuItems";
import {
  chatFolderSiblingNames,
  expandFoldersById,
  findChatAncestorFolderIds,
  findFolderById,
  insertChatFolder,
  maxExpandedFolderDepth,
  toggleChatFolderLevels,
} from "../utils/sidebarTree";

function shouldBlockSidebarHotkey(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return true;
  if (el.closest("input, textarea, select, [contenteditable=true]")) return true;
  if (el.closest(".monaco-editor")) return true;
  if (el.closest(".modal-backdrop, .quick-open-overlay")) return true;
  return false;
}

function chatNameById(
  folders: FolderItem[],
  rootChats: FolderItem["chats"],
  chatId: string,
): string | null {
  const root = rootChats.find((c) => c.id === chatId);
  if (root) return root.name;
  const walk = (items: FolderItem[]): string | null => {
    for (const folder of items) {
      const chat = folder.chats.find((c) => c.id === chatId);
      if (chat) return chat.name;
      const nested = walk(folder.children);
      if (nested) return nested;
    }
    return null;
  };
  return walk(folders);
}

const SIDEBAR_HANDLE_WIDTH = 1;

export interface ChatSidebarHandle {
  createDucky: () => Promise<void>;
  revealFileInSidebar: (path: string) => void;
  revealFileInExplorer: (path: string) => void;
}

export interface ChatSidebarProps {
  isSidebarOpen: boolean;
  sidebarResizeDisabled?: boolean;
  projectSlug: string;
  activeChats: string[];
  activeFilePath?: string;
  syncSidebarPanel?: "chats" | "files";
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  folders: FolderItem[];
  setFolders: React.Dispatch<React.SetStateAction<FolderItem[]>>;
  rootChats: FolderItem["chats"];
  setRootChats: React.Dispatch<React.SetStateAction<FolderItem["chats"]>>;
  archiveChats: FolderItem["chats"];
  setArchiveChats: React.Dispatch<React.SetStateAction<FolderItem["chats"]>>;
  load: () => Promise<void>;
  onChatSelect: (chat: { id: string; name: string }) => void;
  /** Double-click a chat: pin it (permanent tab) instead of the reusable preview tab. */
  onChatOpenPermanent?: (chat: { id: string; name: string }) => void;
  onFocusChat?: (chat: { id: string; name: string }) => void;
  onFileSelect: (path: string, name: string) => void;
  /** Double-click a file: pin it (permanent tab) instead of the reusable preview tab. */
  onFileOpenPermanent?: (path: string, name: string) => void;
  onFocusFile?: (path: string, name: string) => void;
  onFileMoved?: (fromPath: string, toPath: string) => void;
  onChatDeleted?: (chatId: string) => void;
  onChatRenamed?: (chatId: string, name: string) => void;
  onEditDucky?: (chat: { id: string; name: string; duckyStyle?: string; duckyPersonality?: string }) => void;
  onRequestCreateDucky?: (ctx: { folderId: string }) => void;
  filesRefresh?: number;
  onDetachChatAt?: (chat: { id: string; name: string }, at: { screenX: number; screenY: number }) => void;
  onDetachFileAt?: (path: string, name: string, at: { screenX: number; screenY: number }) => void;
  /** Drag from Duckies onto the editor (split zones). Falls back to onChatSelect. */
  onOpenChatInEditor?: (
    chat: { id: string; name: string },
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  /** Drag from Content onto the editor (split zones). Falls back to onFileSelect. */
  onOpenFileInEditor?: (
    path: string,
    name: string,
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  embedded?: boolean;
  dockSide?: DockSide;
  singlePanel?: "chats" | "files";
  /** When embedded as a single dock panel, report header chrome for the outer stacked header. */
  onPanelChrome?: (chrome: ChatSidebarPanelChrome | null) => void;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
  /** Render panel bodies only — tab bar owned by DockRailTabStack. */
  bodiesOnly?: boolean;
  /** Side-wide active panel in bodiesOnly/tabs mode; a body shows only if it matches. */
  dockActivePanelId?: DockPanelId;
  /** All dock panels on this rail when sidebar + verse share one stacked column. */
  unifiedStack?: boolean;
  unifiedStackPanelIds?: DockPanelId[];
  unifiedStackVersePanels?: Partial<
    Record<
      "outline" | "history" | "tester" | "groupchat" | "discordhub",
      {
        title: string;
        icon?: ReactNode;
        actions?: ReactNode;
        children: ReactNode;
      }
    >
  >;
}

export type ChatSidebarPanelChrome = {
  actions: ReactNode;
  onContextMenu?: (e: MouseEvent) => void;
  searchMode?: boolean;
};

type EditTarget = { kind: "folder" | "chat"; id: string; value: string };

const DUCKIES_COMPACT_KEY = "uefn-panel-duckies-compact";

function readDuckiesCompact(): boolean {
  try {
    // Default on — only an explicit "0" opts out.
    return localStorage.getItem(DUCKIES_COMPACT_KEY) !== "0";
  } catch {
    return true;
  }
}

function rememberDuckiesCompact(compact: boolean): void {
  try {
    localStorage.setItem(DUCKIES_COMPACT_KEY, compact ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function SectionIconButton({
  title,
  onClick,
  children,
  buttonRef,
  active = false,
}: {
  title: string;
  onClick: () => void;
  children: ReactNode;
  buttonRef?: Ref<HTMLButtonElement>;
  active?: boolean;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`icon-btn${active ? " is-active" : ""}`}
      title={title}
      aria-label={title}
      aria-pressed={active || undefined}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export const ChatSidebar = forwardRef<ChatSidebarHandle, ChatSidebarProps>(function ChatSidebar(
  {
    isSidebarOpen,
    sidebarResizeDisabled = false,
    projectSlug,
    activeChats,
    activeFilePath,
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
    onChatSelect,
    onChatOpenPermanent,
    onFocusChat,
    onFileSelect,
    onFileOpenPermanent,
    onFocusFile,
    onFileMoved,
    onChatDeleted,
    onChatRenamed,
    onEditDucky,
    onRequestCreateDucky,
    filesRefresh: filesRefreshProp,
    onDetachChatAt,
    onDetachFileAt,
    onOpenChatInEditor,
    onOpenFileInEditor,
    embedded = false,
    dockSide = "left",
    singlePanel,
    onPanelChrome,
    onDockDropZoneChange,
    onDockDragChange,
    bodiesOnly = false,
    dockActivePanelId,
    unifiedStack = false,
    unifiedStackPanelIds,
    unifiedStackVersePanels,
  },
  ref,
) {
  const [panelTab, setPanelTab] = useState<SidebarPanelTab>("chats");
  const [filesRefresh, setFilesRefresh] = useState(0);
  const [contentParentPath, setContentParentPath] = useState("");
  const [selectedChatFolderId, setSelectedChatFolderId] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [newlyCreatedIds, setNewlyCreatedIds] = useState<Set<string>>(new Set());
  const editInputRef = useRef<HTMLInputElement>(null);
  const editSessionRef = useRef<string | null>(null);
  const fileTreeRef = useRef<SidebarFileTreeHandle>(null);
  const lastDuckySelectionRef = useRef<{ kind: "chat" | "folder"; id: string } | null>(null);
  const [verseTemplatePickerOpen, setVerseTemplatePickerOpen] = useState(false);
  const [fileTreeHasExpansion, setFileTreeHasExpansion] = useState(false);
  const [fileTreeSearchOpen, setFileTreeSearchOpen] = useState(false);
  const [fileTreeFilterQuery, setFileTreeFilterQuery] = useState("");
  const debouncedFileTreeFilterQuery = useDebouncedValue(fileTreeFilterQuery, 200);
  const [contentSearchMode, setContentSearchMode] = useState<PanelSearchMode>("filter");
  const [duckyTreeSearchOpen, setDuckyTreeSearchOpen] = useState(false);
  const [duckyTreeFilterQuery, setDuckyTreeFilterQuery] = useState("");
  const debouncedDuckyTreeFilterQuery = useDebouncedValue(duckyTreeFilterQuery, 200);
  const [duckySearchMode, setDuckySearchMode] = useState<PanelSearchMode>("filter");
  const [contentSelectionCount, setContentSelectionCount] = useState(0);
  const [duckySelectionCount, setDuckySelectionCount] = useState(0);
  const shellRef = useRef<HTMLDivElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const stackRef = useRef<HTMLDivElement>(null);
  const sidebarScopeClass = useScopedClass("chat-sidebar");
  const dock = useWorkspaceDockOptional();
  const { mode: legacyPanelMode } = useSidebarPanelMode();
  const panelMode = embedded && dock ? dock.panelModeForSide(dockSide) : legacyPanelMode;
  const legacyStackedLayout = useSidebarPanelLayout();
  const dockStack = dock?.stackForSide(dockSide);
  const sideFilePanels = dock
    ? (dockSide === "left" ? dock.leftPanels : dock.rightPanels).filter(
        (id): id is SidebarPanelTab => id === "chats" || id === "files",
      )
    : (["chats", "files"] as SidebarPanelTab[]);
  const stackedLayout =
    embedded && dock && dockStack
      ? {
          order: sideFilePanels,
          splitRatio: dockStack.splitRatio,
          panelFlex: dockStack.panelFlex,
          collapsed: {
            chats: dockStack.collapsed.chats,
            files: dockStack.collapsed.files,
          },
          focusedPanel: (sideFilePanels.includes(dockStack.focusedPanel as SidebarPanelTab)
            ? dockStack.focusedPanel
            : sideFilePanels[0] ?? "chats") as SidebarPanelTab,
          setFocusedPanel: (id: SidebarPanelTab) => dock.setFocusedPanel(dockSide, id),
          toggleCollapsed: (id: SidebarPanelTab) => dock.toggleCollapsed(dockSide, id),
          swapOrder: () => dock.swapOrder(dockSide),
          swapPanels: (a: SidebarPanelTab, b: SidebarPanelTab) => dock.swapPanels(dockSide, a, b),
          resizeSplit: (splitIndex: number, delta: number, h: number) =>
            dock.resizeSplit(dockSide, splitIndex, delta, h),
          persistSplit: dock.persistSplit,
        }
      : legacyStackedLayout;
  const foldersRef = useRef(folders);
  foldersRef.current = folders;
  const groupCreateQueueRef = useRef<{ chain: Promise<void> }>({ chain: Promise.resolve() });

  const { createDucky } = useCreateDucky({
    folders,
    rootChats,
    load,
    onCreated: onChatSelect,
    filePath: activeFilePath,
    selectedFolderId: selectedChatFolderId,
  });
  const { confirm } = useConfirmModal();
  const undoHistory = useUndoHistoryOptional();
  const { treeRefreshToken, showHiddenFiles, setShowHiddenFiles } = useProjectFilesSettings();
  const [duckiesCompact, setDuckiesCompact] = useState(readDuckiesCompact);
  const toggleDuckiesCompact = useCallback((value: boolean) => {
    setDuckiesCompact(value);
    rememberDuckiesCompact(value);
  }, []);

  useEffect(() => {
    if (filesRefreshProp !== undefined) setFilesRefresh(filesRefreshProp);
  }, [filesRefreshProp]);

  useEffect(() => {
    if (treeRefreshToken > 0) setFilesRefresh((n) => n + 1);
  }, [treeRefreshToken]);

  const dockSetFocusedPanel = dock?.setFocusedPanel;
  const legacySetFocusedPanel = legacyStackedLayout.setFocusedPanel;

  useEffect(() => {
    if (!syncSidebarPanel) return;
    if (panelMode === "tabs") {
      setPanelTab(syncSidebarPanel);
      return;
    }
    if (embedded && dockSetFocusedPanel) {
      dockSetFocusedPanel(dockSide, syncSidebarPanel);
    } else {
      legacySetFocusedPanel(syncSidebarPanel);
    }
    requestAnimationFrame(() => {
      const el = stackRef.current?.querySelector(`[data-panel-id="${syncSidebarPanel}"]`);
      el?.scrollIntoView({ block: "nearest" });
    });
  }, [syncSidebarPanel, panelMode, embedded, dockSide, dockSetFocusedPanel, legacySetFocusedPanel]);

  useEffect(() => {
    if (activeChats.length === 0) return;
    const folderIds = new Set<string>();
    for (const chatId of activeChats) {
      for (const folderId of findChatAncestorFolderIds(folders, rootChats, chatId)) {
        folderIds.add(folderId);
      }
    }
    if (folderIds.size === 0) return;
    setFolders((prev) => {
      const needsExpand = [...folderIds].some((id) => {
        const folder = findFolderById(prev, id);
        return folder && !folder.expanded;
      });
      if (!needsExpand) return prev;
      return expandFoldersById(prev, folderIds);
    });
  }, [activeChats, folders, rootChats, setFolders]);

  useEffect(() => {
    const chatId = activeChats[0];
    if (chatId) lastDuckySelectionRef.current = { kind: "chat", id: chatId };
  }, [activeChats]);

  useEffect(() => {
    if (activeChats.length > 0) setContentParentPath("");
  }, [activeChats]);

  useEffect(() => {
    if (activeFilePath) setSelectedChatFolderId(null);
  }, [activeFilePath]);

  const clearContentSelection = useCallback(() => setContentParentPath(""), []);

  const clearDuckyFolderSelection = useCallback(() => setSelectedChatFolderId(null), []);

  const handleChatSelect = useCallback(
    (chat: { id: string; name: string }) => {
      clearContentSelection();
      lastDuckySelectionRef.current = { kind: "chat", id: chat.id };
      onChatSelect(chat);
    },
    [clearContentSelection, onChatSelect],
  );

  const handleChatOpenPermanent = useCallback(
    (chat: { id: string; name: string }) => {
      clearContentSelection();
      lastDuckySelectionRef.current = { kind: "chat", id: chat.id };
      (onChatOpenPermanent ?? onChatSelect)(chat);
    },
    [clearContentSelection, onChatOpenPermanent, onChatSelect],
  );

  const handleSelectChatFolder = useCallback(
    (folderId: string) => {
      clearContentSelection();
      lastDuckySelectionRef.current = folderId ? { kind: "folder", id: folderId } : null;
      setSelectedChatFolderId(folderId || null);
      // Group folders open the roundtable hub chat — the folder IS the group.
      if (folderId) {
        const folder = findFolderById(foldersRef.current, folderId);
        const hubId = (folder?.groupHubId || "").trim();
        if (hubId) {
          (onChatSelect as (chat: {
            id: string;
            name: string;
            isGroup?: boolean;
          }) => void)({
            id: hubId,
            name: folder?.name || "Group",
            isGroup: true,
          });
        }
      }
    },
    [clearContentSelection, onChatSelect],
  );

  const handleFileSelect = useCallback(
    (path: string, name: string) => {
      clearDuckyFolderSelection();
      onFileSelect(path, name);
    },
    [clearDuckyFolderSelection, onFileSelect],
  );

  const handleFileOpenPermanent = useCallback(
    (path: string, name: string) => {
      clearDuckyFolderSelection();
      (onFileOpenPermanent ?? onFileSelect)(path, name);
    },
    [clearDuckyFolderSelection, onFileOpenPermanent, onFileSelect],
  );

  const handleParentPathChange = useCallback(
    (path: string) => {
      clearDuckyFolderSelection();
      setContentParentPath(path);
    },
    [clearDuckyFolderSelection],
  );

  const textSearch = useSidebarTextSearch({
    folders,
    onFileSelect: handleFileSelect,
    onChatSelect: handleChatSelect,
    lockedScope: "files",
  });

  const duckyTextSearch = useSidebarTextSearch({
    folders,
    onFileSelect: handleFileSelect,
    onChatSelect: handleChatSelect,
    lockedScope: "chats",
  });

  const { width, onResize, persistWidth } = useSidebarWidth();

  const shellWidth = isSidebarOpen ? width + SIDEBAR_HANDLE_WIDTH : 0;

  const markNew = useCallback((kind: "folder" | "chat", id: string) => {
    const key = `${kind}:${id}`;
    setNewlyCreatedIds((prev) => new Set(prev).add(key));
    window.setTimeout(() => {
      setNewlyCreatedIds((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }, 300);
  }, []);

  const createChatFlow = useCallback(async () => {
    if (onRequestCreateDucky) {
      onRequestCreateDucky({ folderId: selectedChatFolderId ?? "" });
      return;
    }
    const created = await createDucky();
    if (created?.id) {
      markNew("chat", created.id);
      setEditing({ kind: "chat", id: created.id, value: created.name });
    }
  }, [createDucky, markNew, onRequestCreateDucky, selectedChatFolderId]);

  const createGroup = useCallback(
    async (parentId: string) => {
      const api = getApi();
      if (!api?.group_create) return;
      const siblings = chatFolderSiblingNames(foldersRef.current, parentId);
      const name = numberedEntryName("Group", siblings);
      const res = await api.group_create(name, parentId);
      if (!res?.ok || !res.id) return;
      const folderId = String((res as { folder_id?: string }).folder_id || "").trim();
      const title = res.title || name;
      if (folderId) {
        // Show the row straight away — load() below only reconciles it, so the
        // group no longer waits on two extra bridge round-trips to appear.
        const next = insertChatFolder(foldersRef.current, parentId, {
          id: folderId,
          name: title,
          expanded: true,
          chats: [],
          children: [],
          groupHubId: res.id,
        });
        foldersRef.current = next;
        setFolders(next);
        markNew("folder", folderId);
      }
      markNew("chat", res.id);
      // Cast: ChatSidebar's onChatSelect type is narrow; ChatView accepts ChatTab.
      (onChatSelect as (chat: {
        id: string;
        name: string;
        isGroup?: boolean;
        groupMembers?: typeof res.group_members;
      }) => void)({
        id: res.id,
        name: title,
        isGroup: true,
        groupMembers: res.group_members || [],
      });
      // The tree selection deliberately stays where it was: it is the create
      // target, and moving it into the new group would nest the next one.
      if (folderId) setEditing({ kind: "folder", id: folderId, value: title });
      else setEditing({ kind: "chat", id: res.id, value: title });
      void load();
    },
    [load, markNew, onChatSelect, setFolders],
  );

  const createGroupFlow = useCallback(
    (folderId?: string) => {
      const parentId = folderId ?? selectedChatFolderId ?? "";
      // Serialized: each create names itself from the tree the previous one left,
      // instead of every click in a fast burst racing to the same "Group1".
      const queue = groupCreateQueueRef.current;
      queue.chain = queue.chain.then(() => createGroup(parentId)).catch(() => undefined);
      return queue.chain;
    },
    [createGroup, selectedChatFolderId],
  );

  const revealFileInSidebar = useCallback(
    (path: string) => {
      if (panelMode === "tabs") {
        setPanelTab("files");
      } else {
        stackedLayout.setFocusedPanel("files");
        requestAnimationFrame(() => {
          const el = stackRef.current?.querySelector(`[data-panel-id="files"]`);
          el?.scrollIntoView({ block: "nearest" });
        });
      }
      requestAnimationFrame(() => fileTreeRef.current?.revealPath(path));
    },
    [panelMode, stackedLayout.setFocusedPanel],
  );

  const revealFileInExplorer = useCallback((path: string) => {
    const api = getApi();
    if (!api?.open_project_path_in_explorer) return;
    void api.open_project_path_in_explorer(path.replace(/\\/g, "/"));
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      createDucky: createChatFlow,
      revealFileInSidebar,
      revealFileInExplorer,
    }),
    [createChatFlow, revealFileInExplorer, revealFileInSidebar],
  );

  const createVerseFolderFlow = useCallback(() => {
    void fileTreeRef.current?.createFolder();
  }, []);

  const createVerseFileFlow = useCallback(() => {
    setVerseTemplatePickerOpen(true);
  }, []);

  const createTextFileFlow = useCallback(() => {
    void fileTreeRef.current?.createTextFile();
  }, []);

  const filesSectionMenuItems = useMemo(
    () => [
      ...fileTreeCreateItems(createVerseFolderFlow, createVerseFileFlow, createTextFileFlow),
      contextMenuSeparator("content-sep-hidden"),
      showHiddenProjectFilesItem(showHiddenFiles, setShowHiddenFiles),
    ],
    [createTextFileFlow, createVerseFileFlow, createVerseFolderFlow, showHiddenFiles, setShowHiddenFiles],
  );

  const duckiesSectionMenuItems = useMemo(
    () => [
      ...duckyTreeCreateItems(
        () => void createChatFlow(),
        () => void createGroupFlow(),
      ),
      contextMenuSeparator("duckies-sep-compact"),
      duckyTreeCompactItem(duckiesCompact, toggleDuckiesCompact),
    ],
    [createChatFlow, createGroupFlow, duckiesCompact, toggleDuckiesCompact],
  );

  const { menu: filesHeaderMenu, open: openFilesHeaderMenu, close: closeFilesHeaderMenu } =
    useContextMenuState<void>();
  const { menu: duckiesHeaderMenu, open: openDuckiesHeaderMenu, close: closeDuckiesHeaderMenu } =
    useContextMenuState<void>();

  const handleVerseTemplateSelect = useCallback((template: VerseTemplate) => {
    setVerseTemplatePickerOpen(false);
    if (template.files && template.files.length > 0) {
      void fileTreeRef.current?.createVerseTemplatePack({
        folder: template.folder,
        files: template.files,
      });
      return;
    }
    void fileTreeRef.current?.createVerseFile(template.content);
  }, []);

  const toggleChatTreeLevel = useCallback(() => {
    setFolders((prev) => toggleChatFolderLevels(prev));
  }, [setFolders]);

  const toggleFileTreeLevel = useCallback(() => {
    fileTreeRef.current?.toggleTreeLevel();
  }, []);

  const chatTreeHasExpansion = maxExpandedFolderDepth(folders) >= 0;

  useEffect(() => {
    if (!editing) {
      editSessionRef.current = null;
      return;
    }
    const sessionKey = `${editing.kind}:${editing.id}`;
    if (editSessionRef.current === sessionKey) return;
    editSessionRef.current = sessionKey;
    editInputRef.current?.focus();
    editInputRef.current?.select();
  }, [editing]);

  const startRename = (kind: "folder" | "chat", id: string, current: string) => {
    setEditing({ kind, id, value: current });
  };

  const renameDuckySelection = useCallback(() => {
    if (editing) return;
    const sel = lastDuckySelectionRef.current;
    if (sel?.kind === "chat") {
      const name = chatNameById(folders, rootChats, sel.id);
      if (name) startRename("chat", sel.id, name);
      return;
    }
    if (sel?.kind === "folder") {
      const folder = findFolderById(folders, sel.id);
      if (folder) startRename("folder", sel.id, folder.name);
      return;
    }
    const chatId = activeChats[0];
    if (chatId) {
      const name = chatNameById(folders, rootChats, chatId);
      if (name) startRename("chat", chatId, name);
      return;
    }
    if (selectedChatFolderId) {
      const folder = findFolderById(folders, selectedChatFolderId);
      if (folder) startRename("folder", selectedChatFolderId, folder.name);
    }
  }, [activeChats, editing, folders, rootChats, selectedChatFolderId]);

  const hotkeyPanel =
    bodiesOnly && embedded && dockStack
      ? ((sideFilePanels.includes(dockStack.focusedPanel as SidebarPanelTab)
          ? dockStack.focusedPanel
          : sideFilePanels[0]) as SidebarPanelTab)
      : panelMode === "tabs"
        ? panelTab
        : stackedLayout.focusedPanel;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isSidebarOpen) return;
      if (shouldBlockSidebarHotkey(e.target)) return;

      if (e.key === "F2") {
        if (hotkeyPanel === "files") {
          if (!fileTreeRef.current?.renameSelection()) return;
          e.preventDefault();
          return;
        }
        if (hotkeyPanel === "chats") {
          renameDuckySelection();
          e.preventDefault();
        }
        return;
      }

      // Copy / Cut / Paste target the Content file tree only.
      if (hotkeyPanel !== "files" || e.altKey || !(e.ctrlKey || e.metaKey)) return;
      const tree = fileTreeRef.current;
      if (!tree) return;
      const key = e.key.toLowerCase();
      if (key === "c" && tree.copySelection()) e.preventDefault();
      else if (key === "x" && tree.cutSelection()) e.preventDefault();
      else if (key === "v" && tree.pasteClipboard()) e.preventDefault();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isSidebarOpen, hotkeyPanel, renameDuckySelection]);

  const commitRename = async () => {
    if (!editing) return;
    const api = getApi();
    const next = editing.value.trim();
    const target = editing;
    setEditing(null);
    if (!api || !next) return;

    if (target.kind === "folder") {
      await api.rename_folder(target.id, next);
    } else {
      await api.rename_conversation(target.id, next);
      onChatRenamed?.(target.id, next);
    }
    void load();
  };

  const cancelRename = () => setEditing(null);

  const deleteChat = async (chatId: string, chatName: string) => {
    const api = getApi();
    if (!api) return;
    // Remember where it lived so Ctrl+Z returns it there, not just to the root.
    const ancestors = findChatAncestorFolderIds(foldersRef.current, rootChats, chatId);
    const prevFolderId = ancestors.length ? ancestors[ancestors.length - 1] : "";
    await api.move_conversation(chatId, ARCHIVE_FOLDER_ID);
    onChatDeleted?.(chatId);
    void load();
    undoHistory?.push({
      label: `Archive ${chatName}`,
      undo: async () => {
        const a = getApi();
        if (!a) return;
        await a.move_conversation(chatId, prevFolderId);
        void load();
      },
      redo: async () => {
        const a = getApi();
        if (!a) return;
        await a.move_conversation(chatId, ARCHIVE_FOLDER_ID);
        void load();
      },
    });
  };

  const returnArchivedChatToActive = async (chatId: string) => {
    const api = getApi();
    if (!api) return;
    // Empty folder id moves the conversation back to the active root (out of Archive).
    await api.move_conversation(chatId, "");
    void load();
  };

  const deleteArchivedChat = async (chatId: string, chatName: string) => {
    if (
      !(await confirm({
        message: `Permanently delete "${chatName}"? This cannot be undone.`,
        confirmLabel: "Delete permanently",
        danger: true,
      }))
    )
      return;
    const api = getApi();
    if (!api) return;
    await api.delete_conversation(chatId);
    onChatDeleted?.(chatId);
    void load();
  };

  const deleteFolder = async (folderId: string, folderName: string) => {
    const folder = findFolderById(foldersRef.current, folderId);
    const hubId = (folder?.groupHubId || "").trim();
    const message = hubId
      ? `Delete group "${folderName}" and any groups nested inside it? Member duckies move to Archive; the group chats are deleted for good.`
      : `Delete folder "${folderName}"? Duckies inside will move to the root.`;
    if (!(await confirm({ message, confirmLabel: "Delete", danger: true }))) return;
    const api = getApi();
    if (!api) return;
    const deletedHubIds = await api.delete_folder(folderId);
    for (const id of deletedHubIds ?? []) onChatDeleted?.(id);
    void load();
  };

  const deleteDuckySelection = async (targets: DuckyDeleteTarget[]): Promise<boolean> => {
    const chats = targets.filter((t) => t.kind === "chat");
    const groups = targets.filter((t) => t.kind === "folder");
    if (chats.length + groups.length < 2) return false;
    const parts = [
      groups.length ? `${groups.length} group${groups.length === 1 ? "" : "s"}` : "",
      chats.length ? `${chats.length} ducky${chats.length === 1 ? "" : "s"}` : "",
    ].filter(Boolean);
    if (
      !(await confirm({
        message: `Delete all ${targets.length} selected items (${parts.join(" and ")})? Duckies move to Archive; groups and anything nested inside them are deleted for good.`,
        confirmLabel: "Delete ALL",
        danger: true,
      }))
    )
      return false;
    const api = getApi();
    if (!api) return false;
    for (const chat of chats) {
      await api.move_conversation(chat.id, ARCHIVE_FOLDER_ID);
      onChatDeleted?.(chat.id);
    }
    const groupIds = new Set(groups.map((g) => g.id));
    const hasSelectedAncestor = (id: string) => {
      let parentId = findFolderById(foldersRef.current, id)?.parentId || "";
      while (parentId) {
        if (groupIds.has(parentId)) return true;
        parentId = findFolderById(foldersRef.current, parentId)?.parentId || "";
      }
      return false;
    };
    for (const group of groups) {
      // Nested groups already went away with their parent's delete.
      if (!findFolderById(foldersRef.current, group.id) || hasSelectedAncestor(group.id)) continue;
      const deletedHubIds = await api.delete_folder(group.id);
      for (const id of deletedHubIds ?? []) onChatDeleted?.(id);
    }
    void load();
    return true;
  };

  const bumpFilesRefresh = useCallback(() => setFilesRefresh((n) => n + 1), []);

  const duckySelectionBadge = formatSelectionBadge(duckySelectionCount);
  const contentSelectionBadge = formatSelectionBadge(contentSelectionCount);

  const duckiesActions = (
    <>
      {duckySelectionBadge ? (
        <span
          className="sidebar-selection-count"
          title={`${duckySelectionCount} selected`}
          aria-label={`${duckySelectionCount} selected`}
        >
          {duckySelectionBadge}
        </span>
      ) : null}
      <SectionIconButton title="New Group Chat" onClick={() => void createGroupFlow()}>
        <Icons.Users />
      </SectionIconButton>
      <SectionIconButton title="New Ducky" onClick={() => void createChatFlow()}>
        <Icons.Plus />
      </SectionIconButton>
      <SectionIconButton
        active={duckyTreeSearchOpen}
        title={duckyTreeSearchOpen ? "Hide search" : "Search duckies or text"}
        onClick={() => {
          setDuckyTreeSearchOpen((open) => {
            const next = !open;
            if (!next) {
              setDuckyTreeFilterQuery("");
              duckyTextSearch.clearSearch();
            }
            return next;
          });
        }}
      >
        <Icons.Search />
      </SectionIconButton>
      <DuckyArchiveDropdown
        archiveChats={archiveChats}
        activeChats={activeChats}
        runningChatIds={runningChatIds}
        completionAlertChatIds={completionAlertChatIds}
        filterQuery={duckyTreeSearchOpen ? duckyTreeFilterQuery : ""}
        onChatSelect={handleChatSelect}
        onReturnToActive={(id) => void returnArchivedChatToActive(id)}
        onDeleteArchivedChat={(id, name) => void deleteArchivedChat(id, name)}
      />
      <SectionIconButton
        title={chatTreeHasExpansion ? "Collapse one level" : "Expand one level"}
        onClick={toggleChatTreeLevel}
      >
        <Icons.CollapseAll />
      </SectionIconButton>
    </>
  );

  const contentActions = (
    <>
      {contentSelectionBadge ? (
        <span
          className="sidebar-selection-count"
          title={`${contentSelectionCount} selected`}
          aria-label={`${contentSelectionCount} selected`}
        >
          {contentSelectionBadge}
        </span>
      ) : null}
      <SectionIconButton title="New Folder" onClick={createVerseFolderFlow}>
        <Icons.FolderPlus />
      </SectionIconButton>
      <SectionIconButton title="New file" onClick={createVerseFileFlow}>
        <Icons.Plus />
      </SectionIconButton>
      <SectionIconButton
        active={fileTreeSearchOpen}
        title={fileTreeSearchOpen ? "Hide search" : "Search files or text"}
        onClick={() => {
          setFileTreeSearchOpen((open) => {
            const next = !open;
            if (!next) {
              setFileTreeFilterQuery("");
              textSearch.clearSearch();
            }
            return next;
          });
        }}
      >
        <Icons.Search />
      </SectionIconButton>
      <SectionIconButton
        title={fileTreeHasExpansion ? "Collapse one level" : "Expand one level"}
        onClick={toggleFileTreeLevel}
      >
        <Icons.CollapseAll />
      </SectionIconButton>
    </>
  );

  const duckiesScroll = (
    <>
      {duckyTreeSearchOpen ? (
        <ContentSearchBar
          mode={duckySearchMode}
          onModeChange={setDuckySearchMode}
          filterLabel="Duckies"
          filterPlaceholder="Filter duckies…"
          filterQuery={duckyTreeFilterQuery}
          onFilterQueryChange={setDuckyTreeFilterQuery}
          onClose={() => {
            setDuckyTreeSearchOpen(false);
            setDuckyTreeFilterQuery("");
            duckyTextSearch.clearSearch();
          }}
          textSearch={duckyTextSearch}
        />
      ) : null}
      <div className="sidebar-panel-scroll">
        {duckySearchMode === "text" && duckyTreeSearchOpen ? (
          <SidebarTextSearchResults search={duckyTextSearch} showFileResults={false} />
        ) : (
          <SidebarFolderTree
            folders={folders}
            setFolders={setFolders}
            rootChats={rootChats}
            setRootChats={setRootChats}
            archiveChats={archiveChats}
            setArchiveChats={setArchiveChats}
            load={load}
            activeChats={activeChats}
            runningChatIds={runningChatIds}
            completionAlertChatIds={completionAlertChatIds}
            onChatSelect={handleChatSelect}
            onChatSelectPersistent={handleChatOpenPermanent}
            newlyCreatedIds={newlyCreatedIds}
            editing={editing}
            setEditing={setEditing}
            editInputRef={editInputRef}
            onCommitRename={() => void commitRename()}
            onCancelRename={cancelRename}
            onRenameFolder={startRename.bind(null, "folder")}
            onDeleteFolder={(id, name) => void deleteFolder(id, name)}
            onRenameChat={startRename.bind(null, "chat")}
            onDeleteChat={(id, name) => void deleteChat(id, name)}
            onDeleteSelection={deleteDuckySelection}
            onFocusChat={onFocusChat ?? (() => {})}
            onEditDucky={onEditDucky ?? (() => {})}
            selectedChatFolderId={selectedChatFolderId}
            onSelectChatFolder={handleSelectChatFolder}
            onCreateDucky={createChatFlow}
            onCreateGroup={createGroupFlow}
            filterQuery={debouncedDuckyTreeFilterQuery}
            onOpenChatInEditor={onOpenChatInEditor ?? handleChatSelect}
            onDetachChatAt={onDetachChatAt}
            dockSide={dockSide}
            onSelectionCountChange={setDuckySelectionCount}
            compact={duckiesCompact}
          />
        )}
      </div>
    </>
  );

  const contentScroll = (
    <>
      {fileTreeSearchOpen ? (
        <ContentSearchBar
          mode={contentSearchMode}
          onModeChange={setContentSearchMode}
          filterLabel="Files"
          filterPlaceholder="Filter files…"
          filterQuery={fileTreeFilterQuery}
          onFilterQueryChange={setFileTreeFilterQuery}
          onClose={() => {
            setFileTreeSearchOpen(false);
            setFileTreeFilterQuery("");
            textSearch.clearSearch();
          }}
          textSearch={textSearch}
        />
      ) : null}
      <div className="sidebar-panel-scroll sidebar-panel-scroll--file-tree">
        {contentSearchMode === "text" && fileTreeSearchOpen ? (
          <SidebarTextSearchResults search={textSearch} showChatResults={false} />
        ) : (
          <ErrorBoundary
            label="File tree"
            resetKeys={[fileTreeFilterQuery, projectSlug]}
            fallback={(error, reset) => (
              <div className="error-boundary-panel error-boundary-panel--inline" role="alert">
                <div className="error-boundary-message">
                  The file list hit an error: {error.message || String(error)}
                </div>
                <button type="button" className="error-boundary-retry" onClick={reset}>
                  Reload files
                </button>
              </div>
            )}
          >
            <SidebarFileTree
              ref={fileTreeRef}
              projectSlug={projectSlug}
              refreshToken={filesRefresh}
              isActive={panelMode === "stacked" || panelTab === "files"}
              activeFilePath={activeFilePath}
              parentPath={contentParentPath}
              onParentPathChange={handleParentPathChange}
              onFileSelect={handleFileSelect}
              onFileOpenPermanent={handleFileOpenPermanent}
              onFocusFile={onFocusFile}
              onFileMoved={onFileMoved}
              onTreeMutated={bumpFilesRefresh}
              onFolderExpansionChange={setFileTreeHasExpansion}
              showHiddenFiles={showHiddenFiles}
              onToggleHiddenFiles={setShowHiddenFiles}
              onNewVerseClass={createVerseFileFlow}
              filterQuery={debouncedFileTreeFilterQuery}
              onOpenFileInEditor={onOpenFileInEditor}
              onDetachFileAt={onDetachFileAt}
              dockSide={dockSide}
              onSelectionCountChange={setContentSelectionCount}
            />
          </ErrorBoundary>
        )}
      </div>
    </>
  );

  const isTabsTextSearchView =
    (panelTab === "files" && contentSearchMode === "text" && fileTreeSearchOpen) ||
    (panelTab === "chats" && duckySearchMode === "text" && duckyTreeSearchOpen);
  const bodyClassName = `sidebar-body${panelMode === "tabs" && isTabsTextSearchView ? " sidebar-body--search" : ""}`;
  const panelHidden = (tab: SidebarPanelTab) => (panelTab === tab ? "" : "sidebar-panel-hidden");

  // Tabs-mode body visibility is decided per-SIDE, not per-family: only the
  // side's active panel (supplied by DockRailFamilyStack) shows. When it's an
  // outline/history tab, none of our chats/files tabs match, so every body here
  // stays hidden — no stray Duckies below the selected panel.
  const bodyHidden = (tab: SidebarPanelTab) =>
    bodiesOnly ? (dockActivePanelId === tab ? "" : "sidebar-panel-hidden") : panelHidden(tab);

  const duckiesBusy = runningChatIds.size > 0;

  const stackedPanelDefs = useMemo(
    () => ({
      chats: {
        title: "Duckies",
        icon: <Icons.Duck />,
        busy: duckiesBusy,
        busyTitle: "Ducky working",
        actions: duckiesActions,
        onContextMenu: openDuckiesHeaderMenu,
        searchMode: duckySearchMode === "text" && duckyTreeSearchOpen,
        children: duckiesScroll,
      },
      files: {
        title: "Content",
        icon: <Icons.Verse />,
        actions: contentActions,
        onContextMenu: openFilesHeaderMenu,
        searchMode: contentSearchMode === "text" && fileTreeSearchOpen,
        children: contentScroll,
      },
    }),
    [
      duckiesBusy,
      duckiesActions,
      contentActions,
      openDuckiesHeaderMenu,
      openFilesHeaderMenu,
      duckySearchMode,
      duckyTreeSearchOpen,
      contentSearchMode,
      fileTreeSearchOpen,
      duckiesScroll,
      contentScroll,
    ],
  );

  useLayoutEffect(() => {
    if (!singlePanel || !onPanelChrome) return;
    const def = stackedPanelDefs[singlePanel];
    onPanelChrome({
      actions: def.actions,
      onContextMenu: def.onContextMenu,
      searchMode: def.searchMode,
    });
    return () => onPanelChrome(null);
  }, [
    singlePanel,
    onPanelChrome,
    duckiesActions,
    contentActions,
    openDuckiesHeaderMenu,
    openFilesHeaderMenu,
    duckySearchMode,
    duckyTreeSearchOpen,
    contentSearchMode,
    fileTreeSearchOpen,
  ]);

  const unifiedStackPanels = useMemo(() => {
    if (!unifiedStack || !unifiedStackPanelIds?.length || !dock || !dockStack) return null;
    const panels: Partial<
      Record<
        DockPanelId,
        {
          title: string;
          icon?: ReactNode;
          busy?: boolean;
          busyTitle?: string;
          actions?: ReactNode;
          onContextMenu?: (e: MouseEvent) => void;
          searchMode?: boolean;
          onTearOffOutside?: (at: { screenX: number; screenY: number }) => void;
          children: ReactNode;
        }
      >
    > = {};
    if (sideFilePanels.includes("chats")) panels.chats = stackedPanelDefs.chats;
    if (sideFilePanels.includes("files")) panels.files = stackedPanelDefs.files;
    if (unifiedStackPanelIds.includes("outline") && unifiedStackVersePanels?.outline) {
      panels.outline = {
        ...unifiedStackVersePanels.outline,
        icon: <Icons.Outline />,
      };
    }
    if (unifiedStackPanelIds.includes("history") && unifiedStackVersePanels?.history) {
      panels.history = {
        ...unifiedStackVersePanels.history,
        icon: <Icons.Clock />,
      };
    }
    if (unifiedStackPanelIds.includes("tester") && unifiedStackVersePanels?.tester) {
      panels.tester = {
        ...unifiedStackVersePanels.tester,
        icon: <Icons.Check />,
      };
    }
    if (unifiedStackPanelIds.includes("groupchat") && unifiedStackVersePanels?.groupchat) {
      panels.groupchat = {
        ...unifiedStackVersePanels.groupchat,
        icon: <Icons.Chat />,
      };
    }
    if (unifiedStackPanelIds.includes("discordhub") && unifiedStackVersePanels?.discordhub) {
      panels.discordhub = {
        ...unifiedStackVersePanels.discordhub,
        icon: <Icons.Chat />,
      };
    }
    return panels as Record<DockPanelId, (typeof panels)[DockPanelId] & { children: ReactNode }>;
  }, [
    unifiedStack,
    unifiedStackPanelIds,
    dock,
    dockStack,
    sideFilePanels,
    stackedPanelDefs,
    unifiedStackVersePanels,
  ]);

  const unifiedStackOrder = useMemo(() => {
    if (!unifiedStackPanelIds?.length || !dockStack) return [];
    const order = dockStack.order.filter((id) => unifiedStackPanelIds.includes(id));
    for (const id of unifiedStackPanelIds) {
      if (!order.includes(id)) order.push(id);
    }
    return order;
  }, [unifiedStackPanelIds, dockStack]);

  const unifiedStackCollapsed = useMemo(() => {
    if (!unifiedStackPanelIds?.length || !dockStack) {
      return {
        chats: false,
        files: false,
        outline: false,
        history: false,
        tester: false,
        groupchat: false,
        discordhub: false,
      };
    }
    return Object.fromEntries(
      unifiedStackPanelIds.map((id) => [id, dockStack.collapsed[id]]),
    ) as Record<DockPanelId, boolean>;
  }, [unifiedStackPanelIds, dockStack]);

  const innerBody = singlePanel ? (
    singlePanel === "chats" ? (
      <>
        {duckiesHeaderMenu ? (
          <ContextMenu
            x={duckiesHeaderMenu.x}
            y={duckiesHeaderMenu.y}
            onClose={closeDuckiesHeaderMenu}
            items={duckiesSectionMenuItems}
          />
        ) : null}
        {duckiesScroll}
      </>
    ) : (
      <>
        {filesHeaderMenu ? (
          <ContextMenu
            x={filesHeaderMenu.x}
            y={filesHeaderMenu.y}
            onClose={closeFilesHeaderMenu}
            items={filesSectionMenuItems}
          />
        ) : null}
        {contentScroll}
      </>
    )
  ) : bodiesOnly ? (
    <>
      {sideFilePanels.includes("chats") ? (
        <div
          ref={targetRef("shell.chat_history", {
            kind: "chat",
            label: "Chat history",
            route: "chat",
          })}
          className={`sidebar-panel-content ${bodyHidden("chats")}`}
          data-dock-panel-id="chats"
        >
          <CtrlWheelZoomRoot className={bodyClassName} storageKey={sidebarPanelZoomKey("chats")}>
            {duckiesHeaderMenu ? (
              <ContextMenu
                x={duckiesHeaderMenu.x}
                y={duckiesHeaderMenu.y}
                onClose={closeDuckiesHeaderMenu}
                items={duckiesSectionMenuItems}
              />
            ) : null}
            <SidebarSectionHeader
              title="Duckies"
              busy={duckiesBusy}
              busyTitle="Ducky working"
              onContextMenu={openDuckiesHeaderMenu}
              actions={duckiesActions}
            />
            {duckiesScroll}
          </CtrlWheelZoomRoot>
        </div>
      ) : null}

      {sideFilePanels.includes("files") ? (
        <div
          className={`sidebar-panel-content ${bodyHidden("files")}`}
          data-dock-panel-id="files"
        >
          <CtrlWheelZoomRoot className={bodyClassName} storageKey={sidebarPanelZoomKey("files")}>
            {filesHeaderMenu ? (
              <ContextMenu
                x={filesHeaderMenu.x}
                y={filesHeaderMenu.y}
                onClose={closeFilesHeaderMenu}
                items={filesSectionMenuItems}
              />
            ) : null}
            <SidebarSectionHeader
              title="Content"
              onContextMenu={openFilesHeaderMenu}
              actions={contentActions}
            />
            {contentScroll}
          </CtrlWheelZoomRoot>
        </div>
      ) : null}
    </>
  ) : unifiedStack && unifiedStackPanels ? (
    <>
      {duckiesHeaderMenu ? (
        <ContextMenu
          x={duckiesHeaderMenu.x}
          y={duckiesHeaderMenu.y}
          onClose={closeDuckiesHeaderMenu}
          items={duckiesSectionMenuItems}
        />
      ) : null}
      {filesHeaderMenu ? (
        <ContextMenu
          x={filesHeaderMenu.x}
          y={filesHeaderMenu.y}
          onClose={closeFilesHeaderMenu}
          items={filesSectionMenuItems}
        />
      ) : null}
      <SidebarStackedPanels<DockPanelId>
        stackRef={stackRef}
        order={unifiedStackOrder}
        splitRatio={dockStack!.splitRatio}
        panelFlex={dockStack!.panelFlex}
        collapsed={unifiedStackCollapsed}
        onToggleCollapsed={(id) => dock!.toggleCollapsed(dockSide, id)}
        onSwapPanels={(panelA, panelB) => dock!.swapPanels(dockSide, panelA, panelB)}
        onResizeSplit={(splitIndex, delta, h) => dock!.resizeSplit(dockSide, splitIndex, delta, h)}
        onPersistSplit={dock!.persistSplit}
        onMovePanelToSide={(panelId, targetSide, insertIndex) =>
          dock!.movePanel(panelId, targetSide, insertIndex)
        }
        dockSide={dockSide}
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
        panels={unifiedStackPanels}
      />
    </>
  ) : panelMode === "tabs" ? (
    <>
      <SidebarPanelTabs
        tabs={[
          ...(sideFilePanels.includes("chats")
            ? [
                {
                  id: "chats" as const,
                  title: "Duckies",
                  icon: <Icons.Duck />,
                  busy: duckiesBusy,
                  busyTitle: "Ducky working",
                },
              ]
            : []),
          ...(sideFilePanels.includes("files")
            ? [{ id: "files" as const, title: "Verse", icon: <Icons.Verse /> }]
            : []),
        ]}
        activeTab={panelTab}
        onSelect={setPanelTab}
        dockSide={embedded ? dockSide : undefined}
        onMovePanel={
          embedded && dock
            ? (panelId, targetSide, _insertIndex, dropTarget) => {
                const insertIndex = insertIndexForTabDrop(dropTarget, targetSide, dock.snapshot);
                dock.movePanel(panelId, targetSide, insertIndex);
              }
            : undefined
        }
        onSwapPanels={embedded && dock ? (a, b) => dock.swapPanels(dockSide, a, b) : undefined}
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
      />

      {sideFilePanels.includes("chats") ? (
        <div
          ref={targetRef("shell.chat_history", {
            kind: "chat",
            label: "Chat history",
            route: "chat",
          })}
          className={`sidebar-panel-content ${panelHidden("chats")}`}
          data-undo-scope="chats"
        >
          <CtrlWheelZoomRoot className={bodyClassName} storageKey={sidebarPanelZoomKey("chats")}>
            {duckiesHeaderMenu ? (
              <ContextMenu
                x={duckiesHeaderMenu.x}
                y={duckiesHeaderMenu.y}
                onClose={closeDuckiesHeaderMenu}
                items={duckiesSectionMenuItems}
              />
            ) : null}
            <SidebarSectionHeader
              title="Duckies"
              busy={duckiesBusy}
              busyTitle="Ducky working"
              onContextMenu={openDuckiesHeaderMenu}
              actions={duckiesActions}
            />
            {duckiesScroll}
          </CtrlWheelZoomRoot>
        </div>
      ) : null}

      {sideFilePanels.includes("files") ? (
        <div className={`sidebar-panel-content ${panelHidden("files")}`}>
          <CtrlWheelZoomRoot className={bodyClassName} storageKey={sidebarPanelZoomKey("files")}>
            {filesHeaderMenu ? (
              <ContextMenu
                x={filesHeaderMenu.x}
                y={filesHeaderMenu.y}
                onClose={closeFilesHeaderMenu}
                items={filesSectionMenuItems}
              />
            ) : null}
            <SidebarSectionHeader
              title="Content"
              onContextMenu={openFilesHeaderMenu}
              actions={contentActions}
            />
            {contentScroll}
          </CtrlWheelZoomRoot>
        </div>
      ) : null}
    </>
  ) : (
    <>
      {duckiesHeaderMenu ? (
        <ContextMenu
          x={duckiesHeaderMenu.x}
          y={duckiesHeaderMenu.y}
          onClose={closeDuckiesHeaderMenu}
          items={duckiesSectionMenuItems}
        />
      ) : null}
      {filesHeaderMenu ? (
        <ContextMenu
          x={filesHeaderMenu.x}
          y={filesHeaderMenu.y}
          onClose={closeFilesHeaderMenu}
          items={filesSectionMenuItems}
        />
      ) : null}
      <SidebarStackedPanels
        stackRef={stackRef}
        order={stackedLayout.order}
        splitRatio={stackedLayout.splitRatio}
        panelFlex={"panelFlex" in stackedLayout ? stackedLayout.panelFlex : undefined}
        collapsed={stackedLayout.collapsed}
        onToggleCollapsed={stackedLayout.toggleCollapsed}
        onSwapPanels={stackedLayout.swapPanels}
        onResizeSplit={stackedLayout.resizeSplit}
        onPersistSplit={stackedLayout.persistSplit}
        dockSide={embedded ? dockSide : undefined}
        onMovePanelToSide={
          embedded && dock
            ? (panelId, targetSide, insertIndex) => dock.movePanel(panelId, targetSide, insertIndex)
            : undefined
        }
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
        panels={stackedPanelDefs}
      />
    </>
  );

  if (embedded) {
    return (
      <>
        {bodiesOnly || unifiedStack ? (
          innerBody
        ) : (
          <div className="dock-rail-sidebar-section">{innerBody}</div>
        )}
        <VerseTemplatePicker
          open={verseTemplatePickerOpen}
          onClose={() => setVerseTemplatePickerOpen(false)}
          onSelect={handleVerseTemplateSelect}
          onSelectTextFile={createTextFileFlow}
        />
      </>
    );
  }

  return (
    <div
      ref={shellRef}
      className={`sidebar-shell chat-sidebar-shell ${sidebarScopeClass} ${isSidebarOpen ? "is-open" : ""}`}
    >
      <ScopedCss
        selector={`.${sidebarScopeClass}`}
        rules={{
          "--sidebar-shell-width": `${shellWidth}px`,
          "--sidebar-width": `${width}px`,
        }}
      />
      <aside ref={asideRef} className={`sidebar-root chat-sidebar-root ${isSidebarOpen ? "is-open" : ""}`}>
        <div ref={innerRef} className="sidebar-inner chat-sidebar-inner">
          {innerBody}
        </div>
      </aside>
      {isSidebarOpen && !sidebarResizeDisabled ? (
        <SplitResizeHandle
          className="sidebar-resize-handle"
          onDrag={onResize}
          onDragEnd={persistWidth}
          ariaLabel="Resize sidebar"
        />
      ) : null}
      <VerseTemplatePicker
        open={verseTemplatePickerOpen}
        onClose={() => setVerseTemplatePickerOpen(false)}
        onSelect={handleVerseTemplateSelect}
        onSelectTextFile={createTextFileFlow}
      />
    </div>
  );
});

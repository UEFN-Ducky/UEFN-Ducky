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
import { SplitResizeHandle } from "./SplitResizeHandle";
import {
  createContext,
  forwardRef,
  memo,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";
import { Icons } from "../icons/Icons";
import { FileTypeIcon } from "../verse-editor/components/FileTypeIcon";
import { isVerseFile, isPanelReadOnlyFile, isWritableContentPath, isSystemWorkspaceRootName, registryKey, UEFN_CORE_SECTION_PATH, WORKSPACE_ROOTS_PATH, workspaceRootDisplayName } from "../verse-editor/utils/isVerseFile";
import { useVerseEditorOptional } from "../verse-editor/VerseEditorProvider";
import { getApi } from "../hooks/usePanelApi";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import { useScopedClass } from "../utils/scopedCss";
import type { EditorDropZone, ProjectFileEntry, WorkspaceRootEntry } from "../types/panel";
import { ContextMenu, useContextMenuState } from "./ContextMenu";
import {
  contextMenuSeparator,
  fileTreeClipboardItems,
  fileTreeCreateItems,
  fileTreeRevealItems,
  showHiddenProjectFilesItem,
} from "../utils/sidebarContextMenuItems";
import {
  CONTENT_ROOT_PATH,
  collapseExpandedPathsOneLevel,
  expandExpandedPathsOneLevel,
  fileDragId,
  fileNestDropId,
  isBrowsableTreeDir,
  isDescendantDir,
  parentDirPath,
  parseFileDragId,
  resolveFileDragOverId,
  resolveFileMoveTarget,
  revealDirPaths,
  setWorkspaceFolderAbsPaths,
} from "../utils/fileTreeDrag";
import { numberedEntryName } from "../utils/numberedEntryName";
import { useFileTreeClipboard } from "../hooks/useFileTreeClipboard";
import { useWatchProjectTree } from "../hooks/useWatchProjectTree";
import {
  emptySelection,
  rangeSelection,
  selectOnly,
  toggleSelection,
  type ExplorerSelection,
} from "../utils/fileTreeSelection";
import {
  collectVisibleFilePaths,
  dirPathsToExpand,
  entryNameMatches,
  shouldShowFileEntry,
} from "../utils/fileTreeFilter";
import { useProjectFileIndex } from "../hooks/useProjectFileIndex";
import { useSidebarDragPointerTracking } from "../hooks/useSidebarDragPointerTracking";
import type { DropPosition } from "../utils/sidebarTree";
import { SidebarTreeChildren } from "./sidebar/SidebarTreeChildren";
import { SidebarTreeRow } from "./sidebar/SidebarTreeRow";
import { computeDropPosition, FileRenameInput, SORTABLE_STATIC } from "./sidebar/sidebarTreeShared";
import { classifySidebarDragOut, type SidebarDragPoint } from "../utils/sidebarDragOut";
import { openVerseTranslatedTab } from "../navigation/openVerseTranslatedTab";
import { canVisualTranslateFile } from "../navigation/tabTranslatePrefs";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import {
  pluginContributesSettingsTab,
  usePluginContributions,
} from "../hooks/usePluginContributions";
import { isEnglishLang } from "../views/settings/translationLanguages";
import { FileTabHoverCard } from "./editor/FileTabHoverCard";
import type { EditorTabHoverCardPlacement } from "../hooks/useEditorTabHoverCard";
import type { DockSide } from "../workspace/workspaceDockStorage";

export const CONTENT_ROOT = CONTENT_ROOT_PATH;
export const WORKSPACE_TREE_ROOT = WORKSPACE_ROOTS_PATH;

/** Sidebar hover cards open away from the rail (right dock → left of the row). */
const FileTreeHoverPlacementContext = createContext<EditorTabHoverCardPlacement>("right");

function hoverPlacementForDockSide(dockSide: DockSide = "left"): EditorTabHoverCardPlacement {
  return dockSide === "right" ? "left" : "right";
}

const FILE_TREE_SPLIT_KEY = "uefn-file-tree-split";
const MIN_FILE_TREE_PANE_PX = 72;
/** Header row + border-top + scroll bottom pad — matches file-tree CSS density. */
const FILE_TREE_CORE_CHROME_PX = 23 + 8;
const FILE_TREE_ROW_PX = 22;

function uefnCoreNaturalHeightPx(fileCount: number): number {
  return FILE_TREE_CORE_CHROME_PX + Math.max(0, fileCount) * FILE_TREE_ROW_PX;
}

function readFileTreeSplitRatio(): number {
  try {
    const n = Number(localStorage.getItem(FILE_TREE_SPLIT_KEY));
    if (Number.isFinite(n)) return Math.min(0.85, Math.max(0.15, n));
  } catch {
    // ignore
  }
  return 0.62;
}

function persistFileTreeSplitRatio(ratio: number) {
  try {
    localStorage.setItem(FILE_TREE_SPLIT_KEY, String(ratio));
  } catch {
    // ignore
  }
}

function mutationParentPath(treePath: string, readOnly?: boolean): string {
  if (readOnly || !isWritableContentPath(treePath)) return treePath;
  if (treePath.toLowerCase().startsWith("ws:")) return CONTENT_ROOT;
  return treePath;
}

export interface VerseTemplatePackInput {
  folder?: string;
  files: Array<{ path: string; content: string }>;
}

export interface SidebarFileTreeHandle {
  createFolder: () => Promise<void>;
  createVerseFile: (content?: string) => Promise<void>;
  createVerseTemplatePack: (pack: VerseTemplatePackInput) => Promise<void>;
  createTextFile: () => Promise<void>;
  refresh: () => Promise<void>;
  hasExpandedFolders: () => boolean;
  toggleTreeLevel: () => void;
  renameSelection: () => boolean;
  revealPath: (path: string) => void;
  /** Ctrl+C / Ctrl+X / Ctrl+V from the sidebar hotkey layer. Return whether it acted. */
  copySelection: () => boolean;
  cutSelection: () => boolean;
  pasteClipboard: () => boolean;
}

type SelectMods = { ctrl: boolean; shift: boolean };

interface SidebarFileTreeProps {
  projectSlug: string;
  refreshToken?: number;
  isActive?: boolean;
  activeFilePath?: string;
  parentPath: string;
  onParentPathChange: (path: string) => void;
  onFileSelect: (path: string, name: string) => void;
  /** Double-click / explicit open: pin the file (permanent tab) rather than preview. */
  onFileOpenPermanent?: (path: string, name: string) => void;
  onFocusFile?: (path: string, name: string) => void;
  onFileMoved?: (fromPath: string, toPath: string) => void;
  onTreeMutated?: () => void;
  onFolderExpansionChange?: (hasExpanded: boolean) => void;
  showHiddenFiles?: boolean;
  onToggleHiddenFiles?: (value: boolean) => void | Promise<void>;
  onNewVerseClass?: () => void;
  filterQuery?: string;
  /** File dropped onto the editor area — open at the VS Code-style zone. */
  onOpenFileInEditor?: (
    path: string,
    name: string,
    placement?: { groupId: string; zone: EditorDropZone },
  ) => void;
  /** File dropped outside the window — tear off into a focus window there. */
  onDetachFileAt?: (path: string, name: string, at: { screenX: number; screenY: number }) => void;
  /** Which dock rail hosts this tree — hover cards open away from the rail. */
  dockSide?: DockSide;
  /** Multi-select size for the Content header badge (2…9 / 9+). */
  onSelectionCountChange?: (count: number) => void;
}

type DirCache = Map<string, ProjectFileEntry[]>;
type DropHint = { overId: string; position: DropPosition };
type EditTarget = { path: string; value: string };

function normTreePath(path: string): string {
  return path.replace(/\\/g, "/");
}

/** True only for an OS/Explorer file drag — internal @dnd-kit drags are pointer-based
 * and never carry a "Files" data type, so this cleanly separates the two. */
function externalDragHasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  for (const t of dt.types) if (t === "Files") return true;
  return false;
}

/** Writable Content folder an external drag is over, or null if it isn't a valid drop
 * target (read-only workspace root, UEFN Core, etc.). Empty space → Content root. */
function resolveExternalDropDir(target: EventTarget | null): string | null {
  const el = target instanceof HTMLElement ? target : null;
  const row = el?.closest<HTMLElement>("[data-file-id]") ?? null;
  if (!row) return CONTENT_ROOT_PATH;
  const parsed = parseFileDragId(row.getAttribute("data-file-id") || "");
  if (!parsed) return CONTENT_ROOT_PATH;
  const dir = parsed.kind === "dir" ? parsed.path : parentDirPath(parsed.path) ?? CONTENT_ROOT_PATH;
  return isWritableContentPath(dir) ? dir : null;
}

function lookupCachedEntry(cache: DirCache, path: string): ProjectFileEntry | null {
  const norm = normTreePath(path);
  if (norm === WORKSPACE_ROOTS_PATH) return null;
  const walk = (dirPath: string): ProjectFileEntry | null => {
    const entries = cache.get(dirPath) ?? [];
    for (const entry of entries) {
      if (normTreePath(entry.path) === norm) return entry;
      if (entry.is_dir && norm.startsWith(`${normTreePath(entry.path)}/`)) {
        const nested = walk(entry.path);
        if (nested) return nested;
      }
    }
    return null;
  };
  return walk(WORKSPACE_ROOTS_PATH);
}

const FileRow = memo(function FileRow({
  entry,
  loading,
  isActive,
  isFocused,
  isEditing,
  editing,
  setEditing,
  editInputRef,
  draggable,
  dropHint,
  onToggle,
  onOpen,
  onOpenPermanent,
  onSelectOnly,
  onModSelect,
  onCopy,
  onCut,
  onPasteInto,
  clipboardHasItems,
  onSelectParent,
  onFocus,
  onRename,
  onDelete,
  deleteLabel = "Delete",
  onCommitRename,
  onCancelRename,
  onPrepareContext,
  onCreateFolder,
  onNewVerseClass,
  onNewFile,
  onRevealInSidebar,
  onRevealInFileExplorer,
  showHiddenFiles,
  onToggleHiddenFiles,
  diagnosticErrors = 0,
  diagnosticWarnings = 0,
  displayName,
}: {
  entry: ProjectFileEntry;
  loading: boolean;
  isActive: boolean;
  isFocused: boolean;
  isEditing: boolean;
  editing: EditTarget | null;
  setEditing: Dispatch<SetStateAction<EditTarget | null>>;
  editInputRef: RefObject<HTMLInputElement>;
  draggable: boolean;
  dropHint: DropHint | null;
  onToggle: () => void;
  onOpen: () => void;
  onOpenPermanent: () => void;
  onSelectOnly: () => void;
  onModSelect: (mods: SelectMods) => void;
  onCopy: () => void;
  onCut: () => void;
  onPasteInto: () => void;
  clipboardHasItems: boolean;
  onSelectParent: () => void;
  onFocus?: () => void;
  onRename?: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onPrepareContext: () => void;
  onCreateFolder: () => void;
  onNewVerseClass: () => void;
  onNewFile: () => void;
  onRevealInSidebar: () => void;
  onRevealInFileExplorer: () => void;
  showHiddenFiles?: boolean;
  onToggleHiddenFiles?: (value: boolean) => void | Promise<void>;
  diagnosticErrors?: number;
  diagnosticWarnings?: number;
  displayName?: string;
}) {
  const hoverPlacement = useContext(FileTreeHoverPlacementContext);
  const id = fileDragId(entry);
  const isDir = entry.is_dir;
  const locked = isPanelReadOnlyFile(entry.path) || entry.read_only === true;
  const systemRoot = isDir && isSystemWorkspaceRootName(entry.name);
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({
    id,
    disabled: !draggable || locked,
    ...SORTABLE_STATIC,
  });
  const rowRef = useRef<HTMLDivElement | null>(null);
  const rowScopeClass = useScopedClass("dnd-row");
  const { menu, open, close } = useContextMenuState<void>();
  const pluginContrib = usePluginContributions();
  const { prefs: translationPrefs } = usePluginUiPrefs("translation");
  const uiLang =
    typeof translationPrefs.language === "string" && translationPrefs.language.trim()
      ? translationPrefs.language.trim()
      : "en";
  // Only when a non-English UI language is active — never show (or route to Settings) on English.
  const showFileTranslate =
    !isDir &&
    canVisualTranslateFile(entry.path) &&
    pluginContributesSettingsTab(pluginContrib, "Languages") &&
    !isEnglishLang(uiLang);

  const mergeRowRef = useCallback(
    (node: HTMLDivElement | null) => {
      setNodeRef(node);
      rowRef.current = node;
    },
    [setNodeRef],
  );

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

  const handleClick = (e: React.MouseEvent) => {
    if (isEditing) return;
    // Ctrl/Meta toggles, Shift range-selects — neither opens or expands (VS Code).
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      onModSelect({ ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
      return;
    }
    onSelectOnly();
    if (isDir) {
      onSelectParent();
      onToggle();
    } else {
      onOpen();
    }
  };

  // Double-clicking a file pins it (VS Code: preview → permanent). Folders ignore it.
  const handleDoubleClick = () => {
    if (isEditing || isDir) return;
    onOpenPermanent();
  };

  const label = loading ? `${displayName ?? entry.name} …` : (displayName ?? entry.name);

  const contextItems = useMemo(() => {
    const canMutate = isWritableContentPath(entry.path) && entry.read_only !== true;
    const pasteTarget = isDir ? entry.path : parentDirPath(entry.path) ?? CONTENT_ROOT;
    const canPasteHere = clipboardHasItems && isWritableContentPath(pasteTarget);
    const items = canMutate
      ? [
          ...fileTreeCreateItems(onCreateFolder, onNewVerseClass, onNewFile),
          contextMenuSeparator("sep-create"),
        ]
      : [];
    items.push(
      ...fileTreeClipboardItems(onCopy, onCut, onPasteInto, canMutate, canPasteHere),
      contextMenuSeparator("sep-clip"),
      ...fileTreeRevealItems(onRevealInSidebar, onRevealInFileExplorer),
      contextMenuSeparator("sep-reveal"),
      ...(isDir || !onFocus ? [] : [{ id: "focus", label: "Focus", onClick: () => onFocus?.() }]),
      ...(onRename && canMutate ? [{ id: "rename", label: "Rename", onClick: onRename }] : []),
      ...(onDelete && canMutate
        ? [{ id: "delete", label: deleteLabel, danger: true, onClick: onDelete }]
        : []),
    );
    if (onToggleHiddenFiles) {
      items.push(
        contextMenuSeparator("sep-hidden"),
        showHiddenProjectFilesItem(showHiddenFiles ?? false, onToggleHiddenFiles),
      );
    }
    return items;
  }, [
    isDir,
    locked,
    entry.path,
    entry.read_only,
    clipboardHasItems,
    onCopy,
    onCut,
    onPasteInto,
    onCreateFolder,
    onDelete,
    deleteLabel,
    onFocus,
    onNewFile,
    onNewVerseClass,
    onRevealInSidebar,
    onRevealInFileExplorer,
    onRename,
    onToggleHiddenFiles,
    showHiddenFiles,
  ]);

  const row = (
    <SidebarTreeRow
      leading={
        isDir ? (
          <span className="sidebar-folder-leading">
            <span className="chevron-icon">
              <Icons.ChevronDown />
            </span>
            <span className="sidebar-tree-row-icon" aria-hidden>
              <Icons.Folder />
            </span>
          </span>
        ) : (
          <span className="sidebar-tree-row-icon">
            <FileTypeIcon
              path={entry.path}
              size={15}
              diagnosticErrors={diagnosticErrors}
              diagnosticWarnings={diagnosticWarnings}
            />
          </span>
        )
      }
      label={label}
      title={isDir ? (displayName ?? entry.name) : entry.path}
      labelClassName={
        [
          systemRoot ? "file-tree-system-root" : "",
          diagnosticErrors > 0
            ? "file-tree-diagnostic-error"
            : diagnosticWarnings > 0
              ? "file-tree-diagnostic-warning"
              : "",
        ]
          .filter(Boolean)
          .join(" ") || undefined
      }
      isEditing={isEditing}
      renameInput={
        <FileRenameInput
          editing={editing}
          setEditing={setEditing}
          editInputRef={editInputRef}
          onCommitRename={onCommitRename}
          onCancelRename={onCancelRename}
          splitExtension={!isDir && isVerseFile(entry.path)}
        />
      }
      contextMenu={
        menu ? (
          <ContextMenu
            x={menu.x}
            y={menu.y}
            onClose={close}
            items={
              showFileTranslate
                ? [
                    {
                      id: "translate",
                      label: "Translate",
                      onClick: () => openVerseTranslatedTab(entry.path),
                    },
                    contextMenuSeparator("sep-translate"),
                    ...contextItems,
                  ]
                : contextItems
            }
          />
        ) : null
      }
      isActive={isActive}
      isFocused={isFocused}
      isDragging={isDragging}
      dropClass={dropClass}
      rowScopeClass={rowScopeClass}
      dndTransform={null}
      dndTransition={undefined}
      mergeRowRef={mergeRowRef}
      dataAttr="data-file-id"
      dataId={id}
      attributes={draggable && !locked ? attributes : undefined}
      listeners={draggable && !locked ? listeners : undefined}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={(e) => {
        if (entry.path === WORKSPACE_ROOTS_PATH) return;
        onPrepareContext();
        open(e, undefined);
      }}
    />
  );

  // Hover card owns Visual translate — never auto-translate on open, and no
  // row-level globe that steals clicks when opening a file.
  if (isDir || isEditing) return row;

  return (
    <FileTabHoverCard
      tab={{
        id,
        kind: "file",
        name: displayName ?? entry.name,
        path: entry.path,
      }}
      diagnosticErrors={diagnosticErrors}
      diagnosticWarnings={diagnosticWarnings}
      disabled={isDragging}
      placement={hoverPlacement}
      showVisualTranslate={showFileTranslate}
    >
      {row}
    </FileTabHoverCard>
  );
});

const FileBranch = memo(function FileBranchImpl({
  entry,
  cache,
  expandedPaths,
  loadingPaths,
  activeFilePath,
  selectedPaths,
  focusPath,
  clipboardHasItems,
  editing,
  setEditing,
  editInputRef,
  dropHint,
  onToggle,
  onOpen,
  onOpenPermanent,
  onSelectEntryOnly,
  onSelectEntryMod,
  onCopyEntry,
  onCutEntry,
  onPasteIntoEntry,
  onSelectParent,
  onFocusFile,
  onRenameEntry,
  onDeleteEntry,
  deletePathsForEntry,
  onCommitRename,
  onCancelRename,
  getFileDiagnosticSummary,
  getFolderDiagnosticSummary,
  onPrepareContextForEntry,
  onCreateFolder,
  onNewVerseClass,
  onNewFile,
  onRevealInSidebar,
  onRevealInFileExplorer,
  showHiddenFiles,
  onToggleHiddenFiles,
  filterQuery = "",
  visiblePaths,
  displayName,
}: {
  entry: ProjectFileEntry;
  cache: DirCache;
  expandedPaths: Set<string>;
  loadingPaths: Set<string>;
  activeFilePath?: string;
  selectedPaths: Set<string>;
  focusPath: string | null;
  clipboardHasItems: boolean;
  editing: EditTarget | null;
  setEditing: Dispatch<SetStateAction<EditTarget | null>>;
  editInputRef: RefObject<HTMLInputElement>;
  dropHint: DropHint | null;
  onToggle: (path: string) => void;
  onOpen: (path: string, name: string) => void;
  onOpenPermanent: (path: string, name: string) => void;
  onSelectEntryOnly: (entry: ProjectFileEntry) => void;
  onSelectEntryMod: (entry: ProjectFileEntry, mods: SelectMods) => void;
  onCopyEntry: (entry: ProjectFileEntry) => void;
  onCutEntry: (entry: ProjectFileEntry) => void;
  onPasteIntoEntry: (entry: ProjectFileEntry) => void;
  onSelectParent: (path: string) => void;
  onFocusFile?: (path: string, name: string) => void;
  onRenameEntry: (path: string, name: string) => void;
  onDeleteEntry: (entry: ProjectFileEntry) => void;
  deletePathsForEntry: (entry: ProjectFileEntry) => string[];
  onCommitRename: () => void;
  onCancelRename: () => void;
  getFileDiagnosticSummary?: (path: string) => { errors: number; warnings: number } | undefined;
  getFolderDiagnosticSummary?: (path: string) => { errors: number; warnings: number };
  onPrepareContextForEntry: (entry: ProjectFileEntry) => void;
  onCreateFolder: () => void;
  onNewVerseClass: () => void;
  onNewFile: () => void;
  onRevealInSidebar: (path: string, isDir: boolean) => void;
  onRevealInFileExplorer: (path: string) => void;
  showHiddenFiles?: boolean;
  onToggleHiddenFiles?: (value: boolean) => void | Promise<void>;
  filterQuery?: string;
  visiblePaths: Set<string>;
  displayName?: string;
}) {
  const filtering = Boolean(filterQuery.trim());
  const expanded = filtering || expandedPaths.has(entry.path);
  const showAllChildren =
    filtering && entry.is_dir && visiblePaths.has(entry.path) && entryNameMatches(filterQuery, entry.name);
  const children = (cache.get(entry.path) ?? []).filter(
    (child) => showAllChildren || shouldShowFileEntry(child, filterQuery, visiblePaths, cache),
  );
  const loading = loadingPaths.has(entry.path);
  // Highlight (is-active bg) covers both multi-selected rows and the open editor file.
  // registryKey: tab paths may omit Content/ or use different casing than the tree.
  const isSelected = selectedPaths.has(entry.path);
  const isActiveFile =
    !entry.is_dir && !!activeFilePath && registryKey(entry.path) === registryKey(activeFilePath);
  const isActive = isSelected || isActiveFile;
  const isFocused = focusPath === entry.path;
  const isEditing = editing?.path === entry.path;
  const draggable = isWritableContentPath(entry.path) && entry.read_only !== true;
  const childSortableIds = children.map((child) => fileDragId(child));
  const { setNodeRef: setNestRef } = useDroppable({ id: fileNestDropId(entry.path) });
  const diag = entry.is_dir
    ? getFolderDiagnosticSummary?.(entry.path)
    : getFileDiagnosticSummary?.(entry.path);
  const deleteCount = deletePathsForEntry(entry).length;

  return (
    <div className={`sidebar-tree-branch ${entry.is_dir && !expanded ? "sidebar-tree-branch-collapsed" : ""}`}>
      <FileRow
        entry={entry}
        loading={loading}
        isActive={isActive}
        isFocused={isFocused}
        isEditing={isEditing}
        editing={editing}
        setEditing={setEditing}
        editInputRef={editInputRef}
        draggable={draggable}
        dropHint={dropHint}
        onToggle={() => onToggle(entry.path)}
        onOpen={() => onOpen(entry.path, entry.name)}
        onOpenPermanent={() => onOpenPermanent(entry.path, entry.name)}
        onSelectOnly={() => onSelectEntryOnly(entry)}
        onModSelect={(mods) => onSelectEntryMod(entry, mods)}
        onCopy={() => onCopyEntry(entry)}
        onCut={() => onCutEntry(entry)}
        onPasteInto={() => onPasteIntoEntry(entry)}
        clipboardHasItems={clipboardHasItems}
        onSelectParent={() => onSelectParent(entry.path)}
        onFocus={!entry.is_dir && onFocusFile ? () => onFocusFile(entry.path, entry.name) : undefined}
        onRename={() => onRenameEntry(entry.path, entry.name)}
        onDelete={() => onDeleteEntry(entry)}
        deleteLabel={deleteCount > 1 ? "Delete ALL" : "Delete"}
        onCommitRename={onCommitRename}
        onCancelRename={onCancelRename}
        onPrepareContext={() => onPrepareContextForEntry(entry)}
        onCreateFolder={onCreateFolder}
        onNewVerseClass={onNewVerseClass}
        onNewFile={onNewFile}
        onRevealInSidebar={() => onRevealInSidebar(entry.path, entry.is_dir)}
        onRevealInFileExplorer={() => onRevealInFileExplorer(entry.path)}
        showHiddenFiles={showHiddenFiles}
        onToggleHiddenFiles={onToggleHiddenFiles}
        diagnosticErrors={diag?.errors ?? 0}
        diagnosticWarnings={diag?.warnings ?? 0}
        displayName={displayName}
      />
      {entry.is_dir ? (
        <SidebarTreeChildren nestRef={setNestRef}>
          <SortableContext items={childSortableIds} strategy={verticalListSortingStrategy}>
            {children.map((child) => (
              <FileBranch
                key={child.path}
                entry={child}
                cache={cache}
                expandedPaths={expandedPaths}
                loadingPaths={loadingPaths}
                activeFilePath={activeFilePath}
                selectedPaths={selectedPaths}
                focusPath={focusPath}
                clipboardHasItems={clipboardHasItems}
                editing={editing}
                setEditing={setEditing}
                editInputRef={editInputRef}
                dropHint={dropHint}
                onToggle={onToggle}
                onOpen={onOpen}
                onOpenPermanent={onOpenPermanent}
                onSelectEntryOnly={onSelectEntryOnly}
                onSelectEntryMod={onSelectEntryMod}
                onCopyEntry={onCopyEntry}
                onCutEntry={onCutEntry}
                onPasteIntoEntry={onPasteIntoEntry}
                onSelectParent={onSelectParent}
                onFocusFile={onFocusFile}
                onRenameEntry={onRenameEntry}
                onDeleteEntry={onDeleteEntry}
                deletePathsForEntry={deletePathsForEntry}
                onCommitRename={onCommitRename}
                onCancelRename={onCancelRename}
                getFileDiagnosticSummary={getFileDiagnosticSummary}
                getFolderDiagnosticSummary={getFolderDiagnosticSummary}
                onPrepareContextForEntry={onPrepareContextForEntry}
                onCreateFolder={onCreateFolder}
                onNewVerseClass={onNewVerseClass}
                onNewFile={onNewFile}
                onRevealInSidebar={onRevealInSidebar}
                onRevealInFileExplorer={onRevealInFileExplorer}
                showHiddenFiles={showHiddenFiles}
                onToggleHiddenFiles={onToggleHiddenFiles}
                filterQuery={filterQuery}
                visiblePaths={visiblePaths}
              />
            ))}
          </SortableContext>
        </SidebarTreeChildren>
      ) : null}
    </div>
  );
});

export const SidebarFileTree = forwardRef<SidebarFileTreeHandle, SidebarFileTreeProps>(
  function SidebarFileTree(
    {
      projectSlug,
      refreshToken = 0,
      isActive = false,
      activeFilePath,
      parentPath,
      onParentPathChange,
      onFileSelect,
      onFileOpenPermanent,
      onFocusFile,
      onFileMoved,
      onTreeMutated,
      onFolderExpansionChange,
      showHiddenFiles = false,
      onToggleHiddenFiles,
      onNewVerseClass,
      filterQuery = "",
      onOpenFileInEditor,
      onDetachFileAt,
      dockSide = "left",
      onSelectionCountChange,
    },
    ref,
  ) {
    const hoverPlacement = hoverPlacementForDockSide(dockSide);
    const [rootEntries, setRootEntries] = useState<ProjectFileEntry[]>([]);
    const [workspaceRoots, setWorkspaceRoots] = useState<WorkspaceRootEntry[]>([]);
    const workspaceRootsRef = useRef(workspaceRoots);
    workspaceRootsRef.current = workspaceRoots;
    const [cache, setCache] = useState<DirCache>(() => new Map());
    const cacheRef = useRef(cache);
    cacheRef.current = cache;
    const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => new Set());
    const expandedPathsRef = useRef(expandedPaths);
    expandedPathsRef.current = expandedPaths;
    const [uefnCoreExpanded, setUefnCoreExpanded] = useState(false);
    const [fileTreeSplitRatio, setFileTreeSplitRatio] = useState(readFileTreeSplitRatio);
    const fileTreeSplitRef = useRef(fileTreeSplitRatio);
    fileTreeSplitRef.current = fileTreeSplitRatio;
    const fileTreeSplitContainerRef = useRef<HTMLDivElement | null>(null);
    const [loadingPaths, setLoadingPaths] = useState<Set<string>>(() => new Set());
    const [error, setError] = useState<string | null>(null);
    const [loadingRoot, setLoadingRoot] = useState(false);
    const [activeDragId, setActiveDragId] = useState<string | null>(null);
    const [dropHint, setDropHint] = useState<DropHint | null>(null);
    // Highlight state for an in-progress Explorer file drop onto the Content root.
    const [externalRootActive, setExternalRootActive] = useState(false);
    const lastExtDirRef = useRef<string | null>(null);
    const [editing, setEditing] = useState<EditTarget | null>(null);
    const [selection, setSelection] = useState<ExplorerSelection>(emptySelection);
    const selectionRef = useRef(selection);
    selectionRef.current = selection;
    useEffect(() => {
      onSelectionCountChange?.(selection.selected.size);
    }, [selection.selected.size, onSelectionCountChange]);
    const { clipboard, copy: setClipboardCopy, cut: setClipboardCut, clear: clearClipboard } =
      useFileTreeClipboard();
    const clipboardRef = useRef(clipboard);
    clipboardRef.current = clipboard;
    const hoverExpandRef = useRef<{ path: string | null; timer: number }>({ path: null, timer: 0 });
    const pointerYRef = useRef(0);
    const dragPointRef = useRef<SidebarDragPoint>({ clientX: 0, clientY: 0, screenX: 0, screenY: 0 });
    const editInputRef = useRef<HTMLInputElement>(null);
    const editSessionRef = useRef<string | null>(null);
    const lastClickedPathRef = useRef<string | null>(null);
    const parentPathRef = useRef(parentPath);
    parentPathRef.current = parentPath;
    const { confirm } = useConfirmModal();
    const undoHistory = useUndoHistoryOptional();
    const verseEditor = useVerseEditorOptional();
    const getFileDiagnosticSummary = verseEditor?.getFileDiagnosticSummary;
    const getFolderDiagnosticSummary = verseEditor?.getFolderDiagnosticSummary;
    const { menu: treeMenu, open: openTreeMenu, close: closeTreeMenu } = useContextMenuState<void>();
    const filtering = Boolean(filterQuery.trim());
    const { files: indexedFiles, reload: reloadFileIndex } = useProjectFileIndex(
      filtering,
      refreshToken,
    );

    const knownFilePaths = useMemo(
      () => new Set(indexedFiles.map((f) => f.path)),
      [indexedFiles],
    );
    const knownFilePathsRef = useRef(knownFilePaths);
    knownFilePathsRef.current = knownFilePaths;

    const visiblePaths = useMemo(
      () => (filtering ? collectVisibleFilePaths(filterQuery, indexedFiles, cache) : new Set<string>()),
      [filterQuery, filtering, indexedFiles, cache],
    );

    const { contentRootEntry, workspaceRootEntries } = useMemo(() => {
      const content = rootEntries.find((entry) => entry.read_only === false) ?? null;
      const workspace = rootEntries.filter((entry) => entry !== content);
      return { contentRootEntry: content, workspaceRootEntries: workspace };
    }, [rootEntries]);

    const contentTopLevelEntries = useMemo(() => {
      if (!contentRootEntry) return [];
      return cache.get(contentRootEntry.path) ?? [];
    }, [contentRootEntry, cache]);

    const filteredContentTreeEntries = useMemo(() => {
      if (!filtering) return contentTopLevelEntries;
      return contentTopLevelEntries.filter((entry) =>
        shouldShowFileEntry(entry, filterQuery, visiblePaths, cache),
      );
    }, [contentTopLevelEntries, filtering, filterQuery, visiblePaths, cache]);

    const contentRootLoading = Boolean(
      contentRootEntry && loadingPaths.has(contentRootEntry.path),
    );

    const filteredUefnCoreFiles = useMemo(() => {
      const files: ProjectFileEntry[] = [];
      for (const root of workspaceRootEntries) {
        for (const child of cache.get(root.path) ?? []) {
          if (!child.is_dir) files.push(child);
        }
      }
      files.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
      if (!filtering) return files;
      return files.filter(
        (file) => visiblePaths.has(file.path) || entryNameMatches(filterQuery, file.name),
      );
    }, [workspaceRootEntries, cache, filtering, filterQuery, visiblePaths]);

    const showUefnCorePane = workspaceRootEntries.length > 0;
    const uefnCoreLoading = workspaceRootEntries.some((root) => loadingPaths.has(root.path));

    const clampFileTreeSplitRatio = useCallback((ratio: number, height: number, fileCount: number) => {
      if (height <= 0) return ratio;
      const minRatio = MIN_FILE_TREE_PANE_PX / height;
      // Content ratio floor: core must not grow past its file-list height.
      const minContentRatio = Math.max(minRatio, 1 - uefnCoreNaturalHeightPx(fileCount) / height);
      const maxContentRatio = 1 - minRatio;
      return Math.max(minContentRatio, Math.min(maxContentRatio, ratio));
    }, []);

    const resizeFileTreeSplit = useCallback((deltaPx: number) => {
      const height = fileTreeSplitContainerRef.current?.clientHeight ?? 0;
      if (height <= 0) return;
      const deltaRatio = deltaPx / height;
      const fileCount = filteredUefnCoreFiles.length;
      setFileTreeSplitRatio((prev) => {
        const next = clampFileTreeSplitRatio(prev + deltaRatio, height, fileCount);
        fileTreeSplitRef.current = next;
        return next;
      });
    }, [clampFileTreeSplitRatio, filteredUefnCoreFiles.length]);

    useEffect(() => {
      if (!uefnCoreExpanded) return;
      const height = fileTreeSplitContainerRef.current?.clientHeight ?? 0;
      if (height <= 0) return;
      setFileTreeSplitRatio((prev) => {
        const next = clampFileTreeSplitRatio(prev, height, filteredUefnCoreFiles.length);
        if (next === prev) return prev;
        fileTreeSplitRef.current = next;
        return next;
      });
    }, [uefnCoreExpanded, filteredUefnCoreFiles.length, clampFileTreeSplitRatio]);

    useEffect(() => {
      setWorkspaceFolderAbsPaths(workspaceRoots.filter((root) => root.read_only).map((root) => root.path));
    }, [workspaceRoots]);

    const prepareContextForEntry = useCallback(
      (entry: ProjectFileEntry) => {
        // Right-clicking a row that isn't selected selects it (VS Code).
        if (!selectionRef.current.selected.has(entry.path)) {
          setSelection(selectOnly(entry.path));
        }
        const target = entry.is_dir
          ? entry.path
          : entry.path.includes("/")
            ? entry.path.replace(/\/[^/]+$/, "")
            : CONTENT_ROOT;
        onParentPathChange(mutationParentPath(target, entry.read_only));
      },
      [onParentPathChange],
    );

    useEffect(() => {
      onFolderExpansionChange?.(expandedPaths.size > 0);
    }, [expandedPaths, onFolderExpansionChange]);

    const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

    const loadDir = useCallback(async (relativePath: string) => {
      const api = getApi();
      if (!api) return [];
      const listing = await api.list_project_files(relativePath || WORKSPACE_ROOTS_PATH);
      return listing.entries;
    }, []);

    const reloadTree = useCallback(async () => {
      const api = getApi();
      if (!api) {
        setRootEntries([]);
        setCache(new Map());
        setError(null);
        return;
      }
      setError(null);
      try {
        if (api.list_workspace_roots) {
          const roots = await api.list_workspace_roots();
          const rootList = Array.isArray(roots) ? roots : [];
          setWorkspaceRoots(rootList);
          setWorkspaceFolderAbsPaths(rootList.filter((root) => root.read_only).map((root) => root.path));
        }
        const entries = await loadDir(WORKSPACE_ROOTS_PATH);
        setRootEntries(entries);
        const nextCache = new Map([[WORKSPACE_ROOTS_PATH, entries]]);
        const wsPaths = entries.filter((entry) => entry.read_only).map((entry) => entry.path);
        const contentWs = entries.find((entry) => !entry.read_only);
        const prefetchPaths = contentWs ? [contentWs.path, ...wsPaths] : wsPaths;
        await Promise.all(
          prefetchPaths.map(async (path) => {
            try {
              nextCache.set(path, await loadDir(path));
            } catch {
              // Workspace root may be unavailable offline.
            }
          }),
        );
        const pathsToRestore = [...expandedPathsRef.current].filter(
          (path) =>
            path !== WORKSPACE_ROOTS_PATH &&
            path !== UEFN_CORE_SECTION_PATH &&
            isBrowsableTreeDir(path),
        );
        await Promise.all(
          pathsToRestore.map(async (path) => {
            try {
              nextCache.set(path, await loadDir(path));
            } catch {
              // Folder may have been moved or deleted since last expand.
            }
          }),
        );
        setCache(nextCache);
        setExpandedPaths((prev) => {
          const next = new Set(prev);
          if (parentPathRef.current && parentPathRef.current !== WORKSPACE_ROOTS_PATH) {
            next.add(parentPathRef.current);
          }
          return next;
        });
      } catch (e) {
        setRootEntries([]);
        setCache(new Map());
        setError(e instanceof Error ? e.message : "Failed to load workspace files");
      }
    }, [loadDir]);

    const initTree = useCallback(async () => {
      setLoadingRoot(true);
      try {
        await reloadTree();
        onParentPathChange(CONTENT_ROOT);
      } finally {
        setLoadingRoot(false);
      }
    }, [reloadTree, onParentPathChange]);

    useEffect(() => {
      void initTree();
    }, [initTree, projectSlug, refreshToken]);

    useEffect(() => {
      if (isActive) void reloadTree();
    }, [isActive, reloadTree]);

    // Auto-refresh the tree when watched folders change on disk (UEFN compile, Explorer,
    // git, …). Watches the Content root + every expanded writable folder; "" fingerprints
    // (workspace roots / abs: dirs) are ignored by the backend.
    const watchedTreeDirs = useMemo(() => {
      const dirs = new Set<string>([CONTENT_ROOT]);
      for (const p of expandedPaths) {
        if (isWritableContentPath(p) && isBrowsableTreeDir(p) && !p.toLowerCase().startsWith("ws:")) {
          dirs.add(p);
        }
      }
      return [...dirs];
    }, [expandedPaths]);

    useWatchProjectTree(watchedTreeDirs, () => void reloadTree(), {
      enabled: isActive && !filtering,
    });

    useEffect(() => {
      if (!activeFilePath) return;
      lastClickedPathRef.current = normTreePath(activeFilePath);
    }, [activeFilePath]);

    useEffect(() => {
      if (!editing) {
        editSessionRef.current = null;
        return;
      }
      const sessionKey = editing.path;
      if (editSessionRef.current === sessionKey) return;
      editSessionRef.current = sessionKey;
      editInputRef.current?.focus();
      editInputRef.current?.select();
    }, [editing]);

    const overIdRef = useRef<string | null>(null);

    const ensureLoaded = useCallback(
      async (dirPath: string) => {
        if (dirPath === UEFN_CORE_SECTION_PATH || !isBrowsableTreeDir(dirPath)) return;
        if (cacheRef.current.has(dirPath)) return;
        setLoadingPaths((prev) => new Set(prev).add(dirPath));
        try {
          const entries = await loadDir(dirPath);
          setCache((prev) => new Map(prev).set(dirPath, entries));
        } catch {
          // Skip stale or out-of-workspace paths during reveal/restore.
        } finally {
          setLoadingPaths((prev) => {
            const next = new Set(prev);
            next.delete(dirPath);
            return next;
          });
        }
      },
      [loadDir],
    );

    useEffect(() => {
      if (!filtering) return;
      void reloadFileIndex();
    }, [filtering, reloadFileIndex]);

    useEffect(() => {
      if (!filtering) return;
      setError(null);
      const expandPaths = dirPathsToExpand(visiblePaths, knownFilePaths);
      if (expandPaths.includes(UEFN_CORE_SECTION_PATH)) {
        setUefnCoreExpanded(true);
      }
      setExpandedPaths((prev) => {
        const next = new Set(prev);
        for (const path of expandPaths) {
          if (path !== UEFN_CORE_SECTION_PATH) next.add(path);
        }
        return next;
      });
      for (const path of expandPaths) {
        if (path !== UEFN_CORE_SECTION_PATH) void ensureLoaded(path);
      }
    }, [filtering, filterQuery, visiblePaths, knownFilePaths, ensureLoaded]);

    const revealPathInTree = useCallback(
      async (filePath: string, isDir = false) => {
        const lower = filePath.replace(/\\/g, "/").toLowerCase();
        const norm =
          [...knownFilePathsRef.current].find((p) => p.toLowerCase() === lower) ??
          filePath.replace(/\\/g, "/");

        // Highlight follows the revealed/active file so the tree tracks the open editor.
        setSelection(selectOnly(norm));

        const cached = lookupCachedEntry(cacheRef.current, norm);
        if (cached) {
          prepareContextForEntry(cached);
        } else if (isDir) {
          onParentPathChange(mutationParentPath(norm));
        } else if (norm.includes("/")) {
          onParentPathChange(mutationParentPath(norm.replace(/\/[^/]+$/, "")));
        }

        const ancestors = revealDirPaths(norm, workspaceRootsRef.current);
        if (ancestors.includes(UEFN_CORE_SECTION_PATH)) {
          setUefnCoreExpanded(true);
        }
        setExpandedPaths((prev) => {
          const next = new Set(prev);
          for (const path of ancestors) {
            if (path !== UEFN_CORE_SECTION_PATH) next.add(path);
          }
          if (isDir) next.add(norm);
          return next;
        });
        const loadPaths = isDir ? [...ancestors, norm] : ancestors;
        for (const path of loadPaths) {
          if (path !== UEFN_CORE_SECTION_PATH) await ensureLoaded(path);
        }
        window.requestAnimationFrame(() => {
          const want = fileDragId({ path: norm, is_dir: isDir }).toLowerCase();
          const el = [...document.querySelectorAll<HTMLElement>("[data-file-id]")].find(
            (n) => (n.getAttribute("data-file-id") || "").toLowerCase() === want,
          );
          el?.scrollIntoView({ block: "nearest" });
        });
      },
      [ensureLoaded, onParentPathChange, prepareContextForEntry],
    );

    const revealPathInFileExplorer = useCallback((path: string) => {
      const api = getApi();
      if (!api?.open_project_path_in_explorer) return;
      void api.open_project_path_in_explorer(path.replace(/\\/g, "/"));
    }, []);

    const revealPathInTreeRef = useRef(revealPathInTree);
    revealPathInTreeRef.current = revealPathInTree;

    useEffect(() => {
      if (!activeFilePath) return;
      void revealPathInTreeRef.current(activeFilePath, false);
    }, [activeFilePath]);

    const refreshAfterMutation = useCallback(
      async (expandPath?: string) => {
        if (expandPath) {
          setExpandedPaths((prev) => {
            const next = new Set(prev);
            next.add(expandPath);
            return next;
          });
        }
        setCache(new Map());
        await reloadTree();
        if (expandPath) {
          await ensureLoaded(expandPath);
        }
        onTreeMutated?.();
      },
      [ensureLoaded, onTreeMutated, reloadTree],
    );

    // Files dropped from Explorer are copied by the native pywebview handler, which then
    // fires this event with the new Content-relative paths so the tree refreshes + selects.
    useEffect(() => {
      const onImported = (evt: Event) => {
        const detail = (evt as CustomEvent).detail as
          | { paths?: string[]; errors?: string[] }
          | undefined;
        const paths = detail?.paths ?? [];
        const errors = detail?.errors ?? [];
        setError(errors.length ? errors.join("; ") : null);
        const first = paths[0];
        const destDir = first ? parentDirPath(first) ?? CONTENT_ROOT : CONTENT_ROOT;
        void (async () => {
          await refreshAfterMutation(destDir);
          if (first) {
            setSelection(selectOnly(first));
            void revealPathInTree(first, false);
          }
        })();
      };
      window.addEventListener("ducky:project-files-imported", onImported);
      return () => window.removeEventListener("ducky:project-files-imported", onImported);
    }, [refreshAfterMutation, revealPathInTree]);

    const handleToggle = useCallback(
      (path: string) => {
        if (path === UEFN_CORE_SECTION_PATH) {
          setUefnCoreExpanded((prev) => !prev);
          return;
        }
        setExpandedPaths((prev) => {
          const next = new Set(prev);
          if (next.has(path)) next.delete(path);
          else next.add(path);
          return next;
        });
        void ensureLoaded(path);
      },
      [ensureLoaded],
    );

    const handleOpen = useCallback(
      (path: string, name: string) => {
        lastClickedPathRef.current = normTreePath(path);
        onFileSelect(path, name);
      },
      [onFileSelect],
    );

    const handleOpenPermanent = useCallback(
      (path: string, name: string) => {
        lastClickedPathRef.current = normTreePath(path);
        (onFileOpenPermanent ?? onFileSelect)(path, name);
      },
      [onFileOpenPermanent, onFileSelect],
    );

    const handleSelectParent = useCallback(
      (path: string) => {
        lastClickedPathRef.current = normTreePath(path);
        onParentPathChange(path);
      },
      [onParentPathChange],
    );

    // Flat order of rendered rows (respects collapsed folders) for shift-range selection.
    const visibleOrder = useCallback((): string[] => {
      return [...document.querySelectorAll<HTMLElement>("[data-file-id]")]
        .map((n) => parseFileDragId(n.getAttribute("data-file-id") || "")?.path)
        .filter((p): p is string => Boolean(p));
    }, []);

    // Keep the toolbar create/paste target (parentPath) in sync with the focused row.
    const syncParentFromFocus = useCallback(
      (entry: ProjectFileEntry) => {
        lastClickedPathRef.current = normTreePath(entry.path);
        const parent = entry.is_dir ? entry.path : parentDirPath(entry.path) ?? CONTENT_ROOT;
        onParentPathChange(mutationParentPath(parent, entry.read_only));
      },
      [onParentPathChange],
    );

    const selectEntryOnly = useCallback(
      (entry: ProjectFileEntry) => {
        setSelection(selectOnly(entry.path));
        syncParentFromFocus(entry);
      },
      [syncParentFromFocus],
    );

    const selectEntryMod = useCallback(
      (entry: ProjectFileEntry, mods: SelectMods) => {
        setSelection((prev) =>
          mods.shift
            ? rangeSelection(prev, entry.path, visibleOrder())
            : toggleSelection(prev, entry.path),
        );
        syncParentFromFocus(entry);
      },
      [syncParentFromFocus, visibleOrder],
    );

    const clearSelection = useCallback(() => {
      setSelection(emptySelection());
      onParentPathChange(CONTENT_ROOT);
    }, [onParentPathChange]);

    const selectedMutablePaths = useCallback(
      (): string[] =>
        [...selectionRef.current.selected].filter(
          (p) => isWritableContentPath(p) && !isPanelReadOnlyFile(p),
        ),
      [],
    );

    // Right-clicking an unselected row acts on just that row; otherwise on the whole selection.
    const clipboardPathsForEntry = useCallback((entry: ProjectFileEntry): string[] => {
      const sel = selectionRef.current;
      const base = sel.selected.has(entry.path) ? [...sel.selected] : [entry.path];
      return base.filter((p) => isWritableContentPath(p) && !isPanelReadOnlyFile(p));
    }, []);

    const copySelection = useCallback((): boolean => {
      const paths = selectedMutablePaths();
      if (!paths.length) return false;
      setClipboardCopy(paths);
      return true;
    }, [selectedMutablePaths, setClipboardCopy]);

    const cutSelection = useCallback((): boolean => {
      const paths = selectedMutablePaths();
      if (!paths.length) return false;
      setClipboardCut(paths);
      return true;
    }, [selectedMutablePaths, setClipboardCut]);

    const copyEntry = useCallback(
      (entry: ProjectFileEntry) => {
        const paths = clipboardPathsForEntry(entry);
        if (paths.length) setClipboardCopy(paths);
      },
      [clipboardPathsForEntry, setClipboardCopy],
    );

    const cutEntry = useCallback(
      (entry: ProjectFileEntry) => {
        const paths = clipboardPathsForEntry(entry);
        if (paths.length) setClipboardCut(paths);
      },
      [clipboardPathsForEntry, setClipboardCut],
    );

    const pasteInto = useCallback(
      async (destParentRaw: string) => {
        const clip = clipboardRef.current;
        if (!clip || !clip.paths.length) return;
        const destParent = mutationParentPath(destParentRaw || CONTENT_ROOT);
        if (!isWritableContentPath(destParent)) return;
        const api = getApi();
        if (!api) return;
        const isCut = clip.mode === "cut";
        let lastPath = "";
        try {
          for (const src of clip.paths) {
            if (isPanelReadOnlyFile(src) || !isWritableContentPath(src)) continue;
            if (isDescendantDir(src, destParent)) continue; // into itself / a descendant
            if (isCut) {
              if (!api.move_project_entry) continue;
              if ((parentDirPath(src) ?? CONTENT_ROOT) === destParent) continue; // no-op move
              const r = await api.move_project_entry(src, destParent);
              lastPath = r.path;
              onFileMoved?.(src, r.path);
            } else {
              if (!api.copy_project_entry) continue;
              const r = await api.copy_project_entry(src, destParent);
              lastPath = r.path;
            }
          }
          if (isCut) clearClipboard();
          await refreshAfterMutation(destParent);
          if (lastPath) setSelection(selectOnly(lastPath));
        } catch (e) {
          setError(e instanceof Error ? e.message : "Failed to paste");
        }
      },
      [clearClipboard, onFileMoved, refreshAfterMutation],
    );

    const pasteIntoEntry = useCallback(
      (entry: ProjectFileEntry) => {
        void pasteInto(entry.is_dir ? entry.path : parentDirPath(entry.path) ?? CONTENT_ROOT);
      },
      [pasteInto],
    );

    const pasteClipboard = useCallback((): boolean => {
      const clip = clipboardRef.current;
      if (!clip || !clip.paths.length) return false;
      const focus = selectionRef.current.focus;
      let target = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
      if (focus) {
        const cached = lookupCachedEntry(cacheRef.current, focus);
        target = cached?.is_dir ? focus : parentDirPath(focus) ?? CONTENT_ROOT;
      }
      void pasteInto(target);
      return true;
    }, [pasteInto]);

    const startRename = useCallback((path: string, name: string) => {
      setEditing({ path, value: name });
    }, []);

    const renameSelection = useCallback((): boolean => {
      if (editing) return false;
      const path =
        lastClickedPathRef.current ??
        (activeFilePath ? normTreePath(activeFilePath) : null) ??
        (parentPath ? normTreePath(parentPath) : null);
      if (!path || path === WORKSPACE_ROOTS_PATH || isPanelReadOnlyFile(path)) return false;

      const cached = lookupCachedEntry(cacheRef.current, path);
      if (cached) {
        if (isPanelReadOnlyFile(cached.path)) return false;
        startRename(cached.path, cached.name);
        return true;
      }

      if (activeFilePath && normTreePath(activeFilePath) === path) {
        const name = path.split("/").pop() || path;
        startRename(path, name);
        return true;
      }

      const folderName = path.split("/").pop();
      if (folderName) {
        startRename(path, folderName);
        return true;
      }
      return false;
    }, [activeFilePath, editing, parentPath, startRename]);

    const cancelRename = useCallback(() => setEditing(null), []);

    const commitRename = useCallback(async () => {
      if (!editing) return;
      const api = getApi();
      const trimmed = editing.value.trim();
      const target = editing;
      setEditing(null);
      if (!api || !trimmed) return;

      const nextName = trimmed;
      const currentName = target.path.split("/").pop() ?? "";
      if (nextName === currentName) return;

      try {
        const result = await api.rename_project_entry(target.path, nextName);
        await refreshAfterMutation(parentPathRef.current);
        onFileMoved?.(target.path, result.path);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to rename");
      }
    }, [editing, onFileMoved, refreshAfterMutation]);

    const handleDeleteEntry = useCallback(
      async (entry: ProjectFileEntry) => {
        const api = getApi();
        if (!api) return;
        const paths = clipboardPathsForEntry(entry);
        if (!paths.length) return;
        const count = paths.length;
        const message =
          count === 1
            ? `Delete "${entry.name}"?`
            : `Delete all ${count} selected items?`;
        const confirmLabel = count === 1 ? "Delete" : "Delete ALL";
        if (!(await confirm({ message, confirmLabel, danger: true }))) return;
        try {
          const trashed: { path: string; token: string | null }[] = [];
          for (const path of paths) {
            const result = await api.delete_project_entry(path);
            trashed.push({ path, token: result.trash_token ?? null });
          }
          await refreshAfterMutation(parentPathRef.current);
          setSelection(emptySelection());
          if (undoHistory && trashed.some((item) => item.token)) {
            const tokens = trashed.map((item) => item.token);
            const pathsCopy = [...paths];
            undoHistory.push({
              label: count === 1 ? `Delete ${entry.name}` : `Delete ${count} items`,
              undo: async () => {
                const a = getApi();
                if (!a) return;
                const restored: string[] = [];
                for (let i = 0; i < trashed.length; i++) {
                  const token = tokens[i];
                  if (!token) continue;
                  const r = await a.restore_trashed_entry(token);
                  tokens[i] = null;
                  restored.push(r.path);
                }
                window.dispatchEvent(
                  new CustomEvent("ducky:project-files-imported", {
                    detail: { paths: restored, errors: [] },
                  }),
                );
              },
              redo: async () => {
                const a = getApi();
                if (!a) return;
                for (let i = 0; i < pathsCopy.length; i++) {
                  const r = await a.delete_project_entry(pathsCopy[i]);
                  tokens[i] = r.trash_token ?? null;
                }
                window.dispatchEvent(
                  new CustomEvent("ducky:project-files-imported", {
                    detail: { paths: [], errors: [] },
                  }),
                );
              },
            });
          }
        } catch (e) {
          setError(e instanceof Error ? e.message : "Failed to delete");
        }
      },
      [clipboardPathsForEntry, confirm, refreshAfterMutation, undoHistory],
    );

    const listSiblingNames = useCallback(async (dirPath: string) => {
      const entries = cache.has(dirPath) ? (cache.get(dirPath) ?? []) : await loadDir(dirPath);
      return entries.map((e) => e.name);
    }, [cache, loadDir]);

    const createFolder = useCallback(async () => {
      const api = getApi();
      if (!api) return;
      const targetParent = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
      if (!isWritableContentPath(targetParent)) return;
      try {
        const siblings = await listSiblingNames(targetParent);
        const name = numberedEntryName("NewFolder", siblings);
        const result = await api.create_project_folder(targetParent, name);
        await refreshAfterMutation(targetParent);
        setEditing({ path: result.path, value: name });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to create folder");
      }
    }, [listSiblingNames, refreshAfterMutation]);

    const createVerseFile = useCallback(async (content?: string) => {
      const api = getApi();
      if (!api) return;
      const targetParent = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
      if (!isWritableContentPath(targetParent)) return;
      try {
        const siblings = await listSiblingNames(targetParent);
        const name = numberedEntryName("VerseClass", siblings, { ext: ".verse" });
        const result = await api.create_project_verse_file(targetParent, name, content ?? "");
        await refreshAfterMutation(targetParent);
        setEditing({ path: result.path, value: name });
        const fileName = result.path.split("/").pop() || name;
        onFileSelect(result.path, fileName);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to create Verse file");
      }
    }, [listSiblingNames, onFileSelect, refreshAfterMutation]);

    const createVerseTemplatePack = useCallback(
      async (pack: VerseTemplatePackInput) => {
        const api = getApi();
        if (!api) return;
        const files = (pack.files || []).filter(
          (f) => f && typeof f.path === "string" && typeof f.content === "string" && f.path.trim(),
        );
        if (!files.length) return;
        const targetParent = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
        if (!isWritableContentPath(targetParent)) return;
        try {
          let packRoot = targetParent;
          const folderBase = (pack.folder || "").trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
          if (folderBase && !folderBase.includes("..")) {
            const siblings = await listSiblingNames(targetParent);
            const folderName = numberedEntryName(folderBase, siblings);
            const folderResult = await api.create_project_folder(targetParent, folderName);
            packRoot = folderResult.path;
          }
          const createdDirs = new Set<string>([packRoot]);
          let firstFilePath = "";
          for (const file of files) {
            const rel = file.path.replace(/\\/g, "/").replace(/^\/+/, "");
            if (!rel || rel.includes("..")) continue;
            const parts = rel.split("/").filter(Boolean);
            if (!parts.length) continue;
            const fileName = parts[parts.length - 1];
            let dirPath = packRoot;
            for (let i = 0; i < parts.length - 1; i++) {
              const seg = parts[i];
              const next = `${dirPath}/${seg}`;
              if (!createdDirs.has(next)) {
                try {
                  const made = await api.create_project_folder(dirPath, seg);
                  createdDirs.add(made.path);
                  dirPath = made.path;
                } catch {
                  // Folder may already exist from a prior file in the pack.
                  createdDirs.add(next);
                  dirPath = next;
                }
              } else {
                dirPath = next;
              }
            }
            const result = await api.create_project_verse_file(dirPath, fileName, file.content);
            if (!firstFilePath) firstFilePath = result.path;
          }
          await refreshAfterMutation(targetParent);
          if (firstFilePath) {
            const fileName = firstFilePath.split("/").pop() || firstFilePath;
            onFileSelect(firstFilePath, fileName);
          }
        } catch (e) {
          setError(e instanceof Error ? e.message : "Failed to create Verse template pack");
        }
      },
      [listSiblingNames, onFileSelect, refreshAfterMutation],
    );

    const createTextFile = useCallback(async () => {
      const api = getApi();
      if (!api) return;
      const targetParent = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
      if (!isWritableContentPath(targetParent)) return;
      try {
        const siblings = await listSiblingNames(targetParent);
        const name = numberedEntryName("NewFile", siblings, { ext: ".txt" });
        const result = await api.create_project_file(targetParent, name, "");
        await refreshAfterMutation(targetParent);
        setEditing({ path: result.path, value: name });
        const fileName = result.path.split("/").pop() || name;
        onFileSelect(result.path, fileName);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to create file");
      }
    }, [listSiblingNames, onFileSelect, refreshAfterMutation]);

    const handleNewVerseClass = useCallback(() => {
      if (onNewVerseClass) {
        onNewVerseClass();
        return;
      }
      void createVerseFile();
    }, [createVerseFile, onNewVerseClass]);

    const treeContextItems = useMemo(() => {
      const parent = mutationParentPath(parentPathRef.current || CONTENT_ROOT);
      const canMutate = isWritableContentPath(parent);
      const items = canMutate
        ? [
            ...fileTreeCreateItems(
              () => void createFolder(),
              () => handleNewVerseClass(),
              () => void createTextFile(),
            ),
          ]
        : [];
      if (onToggleHiddenFiles) {
        items.push(
          contextMenuSeparator("tree-sep-hidden"),
          showHiddenProjectFilesItem(showHiddenFiles, onToggleHiddenFiles),
        );
      }
      return items;
    }, [createFolder, createTextFile, handleNewVerseClass, onToggleHiddenFiles, showHiddenFiles]);

    const rootEntriesRef = useRef(rootEntries);
    rootEntriesRef.current = rootEntries;

    const hasExpandedFolders = useCallback(() => expandedPathsRef.current.size > 0, []);

    const contentRootEntriesRef = useRef<ProjectFileEntry[]>([]);
    contentRootEntriesRef.current = contentTopLevelEntries;

    const toggleTreeLevel = useCallback(() => {
      const prev = expandedPathsRef.current;
      const next =
        prev.size > 0
          ? collapseExpandedPathsOneLevel(prev)
          : expandExpandedPathsOneLevel(prev, cacheRef.current, contentRootEntriesRef.current);
      setExpandedPaths(next);
      for (const path of next) {
        if (!prev.has(path)) void ensureLoaded(path);
      }
    }, [ensureLoaded]);

    useImperativeHandle(
      ref,
      () => ({
        createFolder,
        createVerseFile,
        createVerseTemplatePack,
        createTextFile,
        refresh: reloadTree,
        hasExpandedFolders,
        toggleTreeLevel,
        renameSelection,
        revealPath: (path: string) => {
          void revealPathInTree(path, false);
        },
        copySelection,
        cutSelection,
        pasteClipboard,
      }),
      [
        createFolder,
        createVerseFile,
        createVerseTemplatePack,
        createTextFile,
        hasExpandedFolders,
        reloadTree,
        renameSelection,
        revealPathInTree,
        toggleTreeLevel,
        copySelection,
        cutSelection,
        pasteClipboard,
      ],
    );

    // VS Code: hovering a collapsed folder for ~400ms during a drag expands it.
    const scheduleAutoExpand = useCallback(
      (dirPath: string | null) => {
        if (hoverExpandRef.current.path === dirPath) return;
        if (hoverExpandRef.current.timer) window.clearTimeout(hoverExpandRef.current.timer);
        hoverExpandRef.current = { path: dirPath, timer: 0 };
        if (!dirPath || !isBrowsableTreeDir(dirPath) || expandedPathsRef.current.has(dirPath)) return;
        hoverExpandRef.current.timer = window.setTimeout(() => {
          setExpandedPaths((prev) => new Set(prev).add(dirPath));
          void ensureLoaded(dirPath);
        }, 400);
      },
      [ensureLoaded],
    );

    const collisionDetection = useCallback<CollisionDetection>((args) => {
      // pointerWithin gives accurate nested-folder targeting; closestCenter fallback still
      // resolves an `over` far outside the tree, which the drag-out path relies on.
      const within = pointerWithin(args);
      return within.length ? within : closestCenter(args);
    }, []);

    const updateDropHint = useCallback(
      (overId: string | null) => {
        overIdRef.current = overId;
        if (!overId) {
          setDropHint(null);
          scheduleAutoExpand(null);
          return;
        }
        const nestDirPath = overId.startsWith("nest:") ? overId.slice(5) : null;
        if (nestDirPath !== null) {
          const nestId = fileNestDropId(nestDirPath);
          const targetId = fileDragId({ path: nestDirPath, is_dir: true });
          // Root nest lights the whole tree; nested nests light the folder row.
          const isRootNest =
            nestDirPath === WORKSPACE_ROOTS_PATH ||
            nestDirPath === CONTENT_ROOT ||
            nestDirPath === (contentRootEntry?.path ?? "");
          setDropHint({ overId: isRootNest ? nestId : targetId, position: "inside" });
          scheduleAutoExpand(isRootNest ? null : nestDirPath);
          return;
        }

        const parsed = parseFileDragId(overId);
        if (!parsed) {
          setDropHint(null);
          scheduleAutoExpand(null);
          return;
        }

        const el = document.querySelector(`[data-file-id="${overId}"]`);
        if (!el) {
          setDropHint(null);
          scheduleAutoExpand(null);
          return;
        }
        const rect = el.getBoundingClientRect();
        const position = computeDropPosition(pointerYRef.current, rect, parsed.kind === "dir");
        setDropHint({ overId, position });
        scheduleAutoExpand(parsed.kind === "dir" && position === "inside" ? parsed.path : null);
      },
      [scheduleAutoExpand, contentRootEntry?.path],
    );

    const refreshDropHint = useCallback(() => {
      updateDropHint(overIdRef.current);
    }, [updateDropHint]);

    useSidebarDragPointerTracking(activeDragId, pointerYRef, dragPointRef, setDropHint, refreshDropHint);

    const handleDragOver = (event: DragOverEvent) => {
      const overId = event.over ? String(event.over.id) : null;
      updateDropHint(overId);
    };

    const handleDragEnd = async (event: DragEndEvent) => {
      const { active, over } = event;
      setActiveDragId(null);
      const hint = dropHint;
      setDropHint(null);
      overIdRef.current = null;
      scheduleAutoExpand(null);

      // Drag left the sidebar: open instead of move. closestCenter keeps `over`
      // pointing at the nearest row even far outside the tree, so the pointer
      // position — not `over` — decides this.
      const dragOutZone = classifySidebarDragOut(dragPointRef.current);
      if (dragOutZone) {
        const parsed = parseFileDragId(String(active.id));
        if (parsed?.kind === "file") {
          const name = activeLabel || parsed.path.split("/").pop() || parsed.path;
          if (dragOutZone.kind === "editor") {
            const placement = { groupId: dragOutZone.groupId, zone: dragOutZone.zone };
            if (onOpenFileInEditor) onOpenFileInEditor(parsed.path, name, placement);
            else onFileSelect(parsed.path, name);
          } else {
            onDetachFileAt?.(parsed.path, name, {
              screenX: dragOutZone.screenX,
              screenY: dragOutZone.screenY,
            });
          }
        }
        return;
      }

      if (!over && !hint) return;

      let overId = hint?.overId ?? (over ? String(over.id) : null);
      if (!overId) return;
      if (hint) {
        overId = resolveFileDragOverId(cache, hint.overId, hint.position);
      } else if (!overId.startsWith("nest:")) {
        const parsed = parseFileDragId(overId);
        if (parsed?.kind === "dir") {
          const el = document.querySelector(`[data-file-id="${overId}"]`);
          if (el) {
            const rect = el.getBoundingClientRect();
            const pos = computeDropPosition(pointerYRef.current, rect, true);
            overId = resolveFileDragOverId(cache, overId, pos);
          }
        }
      }

      const activeParsed = parseFileDragId(String(active.id));
      if (!activeParsed) return;

      const destParent = resolveFileMoveTarget(
        activeParsed.path,
        activeParsed.kind === "dir",
        overId,
      );
      if (!destParent) return;

      const api = getApi();
      if (!api?.move_project_entry) return;

      // If the dragged row is part of a multi-selection, move the whole writable set.
      const sel = selectionRef.current.selected;
      const sources = sel.has(activeParsed.path) && sel.size > 1 ? [...sel] : [activeParsed.path];

      try {
        let lastPath = "";
        for (const src of sources) {
          if (isPanelReadOnlyFile(src) || !isWritableContentPath(src)) continue;
          const cached = lookupCachedEntry(cache, src);
          const srcIsDir = cached?.is_dir ?? (src === activeParsed.path && activeParsed.kind === "dir");
          if (srcIsDir && (destParent === src || isDescendantDir(src, destParent))) continue;
          if ((parentDirPath(src) ?? CONTENT_ROOT) === destParent) continue; // no-op move
          const result = await api.move_project_entry(src, destParent);
          lastPath = result.path;
          onFileMoved?.(src, result.path);
        }
        await refreshAfterMutation(destParent);
        if (lastPath) setSelection(selectOnly(lastPath));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to move item");
      }
    };

    // --- Explorer → Content tree file drop (copy). The internal @dnd-kit drag above is
    // pointer-based; these native HTML5 handlers only engage for real OS file drags. The
    // actual copy runs in Python (file_drop_import.py) — only it can see the file paths —
    // so here we just resolve/highlight the destination folder and report it to the backend.
    const applyExternalHint = useCallback((dir: string | null) => {
      if (!dir) {
        setDropHint(null);
        setExternalRootActive(false);
        return;
      }
      if (normTreePath(dir) === normTreePath(CONTENT_ROOT)) {
        setDropHint(null);
        setExternalRootActive(true);
      } else {
        setDropHint({ overId: fileDragId({ path: dir, is_dir: true }), position: "inside" });
        setExternalRootActive(false);
      }
    }, []);

    const reportExternalDropTarget = useCallback((dir: string | null) => {
      getApi()?.set_import_drop_target?.(dir ?? "")?.catch?.(() => {});
    }, []);

    const clearExternalDrag = useCallback(() => {
      lastExtDirRef.current = null;
      applyExternalHint(null);
      reportExternalDropTarget(null);
    }, [applyExternalHint, reportExternalDropTarget]);

    const handleExternalDragOver = useCallback(
      (e: React.DragEvent<HTMLDivElement>) => {
        if (!externalDragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        const dir = resolveExternalDropDir(e.target);
        e.dataTransfer.dropEffect = dir ? "copy" : "none";
        if (lastExtDirRef.current !== (dir ?? "")) {
          lastExtDirRef.current = dir ?? "";
          applyExternalHint(dir);
          reportExternalDropTarget(dir);
        }
      },
      [applyExternalHint, reportExternalDropTarget],
    );

    const handleExternalDragLeave = useCallback(
      (e: React.DragEvent<HTMLDivElement>) => {
        if (!externalDragHasFiles(e.dataTransfer)) return;
        // Ignore row-to-row moves; only clear when the pointer leaves the tree entirely.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        clearExternalDrag();
      },
      [clearExternalDrag],
    );

    const handleExternalDrop = useCallback(
      (e: React.DragEvent<HTMLDivElement>) => {
        if (!externalDragHasFiles(e.dataTransfer)) return;
        // Suppress WebView2's default (navigate to / open the dropped file). The native
        // drop handler reads set_import_drop_target right after and performs the copy, so
        // we clear only the local highlight — not the reported target.
        e.preventDefault();
        applyExternalHint(null);
        lastExtDirRef.current = null;
      },
      [applyExternalHint],
    );

    const activeLabel = useMemo(() => {
      if (!activeDragId) return "";
      const parsed = parseFileDragId(activeDragId);
      if (!parsed) return "";
      const walk = (dirPath: string): string => {
        const entries = cache.get(dirPath) ?? [];
        for (const entry of entries) {
          if (entry.path === parsed.path) return entry.name;
          if (entry.is_dir) {
            const nested = walk(entry.path);
            if (nested) return nested;
          }
        }
        return "";
      };
      if (parsed.path === WORKSPACE_ROOTS_PATH) return "Workspace";
      const rootEntry = rootEntriesRef.current.find((entry) => entry.path === parsed.path);
      if (rootEntry) return workspaceRootDisplayName(rootEntry);
      return walk(WORKSPACE_ROOTS_PATH) || parsed.path.split("/").pop() || "";
    }, [activeDragId, cache]);

    const activeDragIsDir = useMemo(() => {
      if (!activeDragId) return false;
      return parseFileDragId(activeDragId)?.kind === "dir";
    }, [activeDragId]);

    const dragCount = useMemo(() => {
      if (!activeDragId) return 1;
      const path = parseFileDragId(activeDragId)?.path;
      return path && selection.selected.has(path) ? selection.selected.size : 1;
    }, [activeDragId, selection]);

    const rootSortableIds = useMemo(() => {
      const ids = filteredContentTreeEntries.map((entry) => fileDragId(entry));
      if (uefnCoreExpanded || filtering) {
        ids.push(...filteredUefnCoreFiles.map((file) => fileDragId(file)));
      }
      return ids;
    }, [filteredContentTreeEntries, filteredUefnCoreFiles, filtering, uefnCoreExpanded]);

    const contentDropPath = contentRootEntry?.path ?? WORKSPACE_ROOTS_PATH;
    const { setNodeRef: setRootDropRef, isOver: rootIsOver } = useDroppable({
      id: fileNestDropId(contentDropPath),
    });

    if (loadingRoot) {
      return <div className="ui-status-sidebar-muted">Loading workspace…</div>;
    }

    if (error && rootEntries.length === 0) {
      return <div className="ui-status-sidebar-error">{error}</div>;
    }

    if (rootEntries.length === 0) {
      return (
        <>
          <div
            className="ui-status-sidebar-muted file-tree-empty"
            onContextMenu={(e) => {
              onParentPathChange(CONTENT_ROOT);
              openTreeMenu(e, undefined);
            }}
          >
            No workspace folders found. Select a UEFN project with a Verse workspace.
          </div>
          {treeMenu ? (
            <ContextMenu x={treeMenu.x} y={treeMenu.y} onClose={closeTreeMenu} items={treeContextItems} />
          ) : null}
        </>
      );
    }

    if (filtering && filteredContentTreeEntries.length === 0 && filteredUefnCoreFiles.length === 0) {
      return (
        <div className="ui-status-sidebar-muted file-tree-empty">
          No files match &ldquo;{filterQuery.trim()}&rdquo;
        </div>
      );
    }

    const fileBranchProps = {
      cache,
      expandedPaths,
      loadingPaths,
      activeFilePath,
      selectedPaths: selection.selected,
      focusPath: selection.focus,
      clipboardHasItems: (clipboard?.paths.length ?? 0) > 0,
      editing,
      setEditing,
      editInputRef,
      dropHint,
      onToggle: handleToggle,
      onOpen: handleOpen,
      onOpenPermanent: handleOpenPermanent,
      onSelectEntryOnly: selectEntryOnly,
      onSelectEntryMod: selectEntryMod,
      onCopyEntry: copyEntry,
      onCutEntry: cutEntry,
      onPasteIntoEntry: pasteIntoEntry,
      onSelectParent: handleSelectParent,
      onFocusFile,
      onRenameEntry: startRename,
      onDeleteEntry: handleDeleteEntry,
      deletePathsForEntry: clipboardPathsForEntry,
      onCommitRename: () => void commitRename(),
      onCancelRename: cancelRename,
      getFileDiagnosticSummary,
      getFolderDiagnosticSummary,
      onPrepareContextForEntry: prepareContextForEntry,
      onCreateFolder: () => void createFolder(),
      onNewVerseClass: handleNewVerseClass,
      onNewFile: () => void createTextFile(),
      onRevealInSidebar: revealPathInTree,
      onRevealInFileExplorer: revealPathInFileExplorer,
      showHiddenFiles,
      onToggleHiddenFiles,
      filterQuery,
      visiblePaths,
    } as const;

    const contentPane = (
      <>
        {contentRootLoading && filteredContentTreeEntries.length === 0 ? (
          <div className="ui-status-sidebar-muted file-tree-empty">Loading…</div>
        ) : null}
        {filteredContentTreeEntries.map((entry) => (
          <FileBranch key={entry.path} entry={entry} {...fileBranchProps} />
        ))}
        {!contentRootLoading && !filtering && filteredContentTreeEntries.length === 0 ? (
          <div className="ui-status-sidebar-muted file-tree-empty">
            This folder is empty — right-click to create a file.
          </div>
        ) : null}
      </>
    );

    const uefnCoreFilesList = filteredUefnCoreFiles.map((file) => {
      const isActive = !!activeFilePath && registryKey(file.path) === registryKey(activeFilePath);
      const diag = getFileDiagnosticSummary?.(file.path);
      return (
        <FileRow
          key={file.path}
          entry={file}
          loading={false}
          isActive={isActive || selection.selected.has(file.path)}
          isFocused={selection.focus === file.path}
          isEditing={editing?.path === file.path}
          editing={editing}
          setEditing={setEditing}
          editInputRef={editInputRef}
          draggable={false}
          dropHint={dropHint}
          onToggle={() => {}}
          onOpen={() => handleOpen(file.path, file.name)}
          onOpenPermanent={() => handleOpenPermanent(file.path, file.name)}
          onSelectOnly={() => {}}
          onModSelect={() => {}}
          onCopy={() => copyEntry(file)}
          onCut={() => cutEntry(file)}
          onPasteInto={() => pasteIntoEntry(file)}
          clipboardHasItems={(clipboard?.paths.length ?? 0) > 0}
          onSelectParent={() => {}}
          onFocus={onFocusFile ? () => onFocusFile(file.path, file.name) : undefined}
          onCommitRename={() => void commitRename()}
          onCancelRename={cancelRename}
          onPrepareContext={() => prepareContextForEntry(file)}
          onCreateFolder={() => void createFolder()}
          onNewVerseClass={handleNewVerseClass}
          onNewFile={() => void createTextFile()}
          onRevealInSidebar={() => revealPathInTree(file.path, false)}
          onRevealInFileExplorer={() => revealPathInFileExplorer(file.path)}
          showHiddenFiles={showHiddenFiles}
          onToggleHiddenFiles={onToggleHiddenFiles}
          diagnosticErrors={diag?.errors ?? 0}
          diagnosticWarnings={diag?.warnings ?? 0}
        />
      );
    });

    const useSplitLayout = showUefnCorePane && !filtering;
    const coreMaxHeightStyle = uefnCoreExpanded
      ? ({ ["--file-tree-core-max-height"]: `${(1 - fileTreeSplitRatio) * 100}%` } as CSSProperties)
      : undefined;

    return (
      <FileTreeHoverPlacementContext.Provider value={hoverPlacement}>
      <div className="file-tree-shell" data-undo-scope="files">
        {error ? <div className="ui-status-sidebar-error">{error}</div> : null}
        {treeMenu ? (
          <ContextMenu x={treeMenu.x} y={treeMenu.y} onClose={closeTreeMenu} items={treeContextItems} />
        ) : null}
        <DndContext
          sensors={sensors}
          collisionDetection={collisionDetection}
          onDragStart={(e) => {
            setActiveDragId(String(e.active.id));
            if (e.activatorEvent && "clientY" in e.activatorEvent) {
              const pe = e.activatorEvent as PointerEvent;
              pointerYRef.current = pe.clientY;
              dragPointRef.current = { clientX: pe.clientX, clientY: pe.clientY, screenX: pe.screenX, screenY: pe.screenY };
            }
          }}
          onDragOver={handleDragOver}
          onDragEnd={(e) => void handleDragEnd(e)}
          onDragCancel={() => {
            setActiveDragId(null);
            setDropHint(null);
            overIdRef.current = null;
            scheduleAutoExpand(null);
          }}
        >
          <div
            ref={setRootDropRef}
            className={`file-tree file-tree-root ${rootIsOver || externalRootActive || dropHint?.overId === fileNestDropId(contentDropPath) ? "sidebar-drop-root-active" : ""} ${useSplitLayout ? "file-tree--split" : ""}`}
            onDragEnter={handleExternalDragOver}
            onDragOver={handleExternalDragOver}
            onDragLeave={handleExternalDragLeave}
            onDrop={handleExternalDrop}
            onClick={(e) => {
              const el = e.target as HTMLElement;
              if (el.closest("[data-file-id]") || el.closest(".file-tree-section-header-row")) return;
              clearSelection();
            }}
            onContextMenu={(e) => {
              if ((e.target as HTMLElement).closest("[data-file-id]")) return;
              onParentPathChange(parentPathRef.current || CONTENT_ROOT);
              openTreeMenu(e, undefined);
            }}
          >
            <SortableContext items={rootSortableIds} strategy={verticalListSortingStrategy}>
              {useSplitLayout ? (
                <div ref={fileTreeSplitContainerRef} className="file-tree-split">
                  <div className="file-tree-pane file-tree-pane--content">
                    <div className="file-tree-pane-scroll">{contentPane}</div>
                  </div>
                  {uefnCoreExpanded ? (
                    <SplitResizeHandle
                      className="file-tree-pane-split"
                      orientation="vertical"
                      ariaLabel="Resize Content and UEFN Core"
                      onDrag={resizeFileTreeSplit}
                      onDragEnd={() => persistFileTreeSplitRatio(fileTreeSplitRef.current)}
                    />
                  ) : null}
                  <div
                    className={`file-tree-pane file-tree-pane--core sidebar-tree-branch${uefnCoreExpanded ? "" : " sidebar-tree-branch-collapsed file-tree-pane--core-collapsed"}`}
                    style={coreMaxHeightStyle}
                  >
                    <div
                      className="sidebar-tree-row file-tree-section-header-row"
                      title={uefnCoreExpanded ? "Collapse UEFN Core" : "Expand UEFN Core"}
                      onClick={() => handleToggle(UEFN_CORE_SECTION_PATH)}
                    >
                      <span className="file-tree-section-label file-tree-section-label--header">
                        {uefnCoreLoading ? "UEFN Core …" : "UEFN Core"}
                      </span>
                    </div>
                    <div
                      className="file-tree-pane-scroll file-tree-uefn-core-children"
                      aria-hidden={!uefnCoreExpanded || undefined}
                    >
                      {uefnCoreFilesList}
                    </div>
                  </div>
                </div>
              ) : (
                <>
                  {contentPane}
                  {showUefnCorePane ? (
                    <div
                      className={`file-tree-uefn-core-section sidebar-tree-branch ${!uefnCoreExpanded ? "sidebar-tree-branch-collapsed" : ""}`}
                    >
                      <div
                        className="sidebar-tree-row file-tree-section-header-row"
                        onClick={() => handleToggle(UEFN_CORE_SECTION_PATH)}
                      >
                        <span className="file-tree-section-label file-tree-section-label--header">
                          {uefnCoreLoading ? "UEFN Core …" : "UEFN Core"}
                        </span>
                      </div>
                      <div className="sidebar-tree-children-clip">
                        <div className="sidebar-tree-children file-tree-uefn-core-children">{uefnCoreFilesList}</div>
                      </div>
                    </div>
                  ) : null}
                </>
              )}
            </SortableContext>
          </div>
          <DragOverlay>
            {activeDragId ? (
              <div className="sidebar-drag-overlay">
                {activeDragIsDir ? (
                  <Icons.Folder />
                ) : activeDragId ? (
                  <FileTypeIcon path={parseFileDragId(activeDragId)?.path ?? ""} size={14} />
                ) : (
                  <Icons.File />
                )}
                <span>{activeLabel}</span>
                {dragCount > 1 ? <span className="sidebar-drag-count">{dragCount}</span> : null}
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </div>
      </FileTreeHoverPlacementContext.Provider>
    );
  },
);

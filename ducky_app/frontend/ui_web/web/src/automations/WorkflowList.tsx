import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type Ref } from "react";
import { getApi } from "../hooks/usePanelApi";
import type { AutomationSummaryDto, AutomationTemplateDto, PluginScopeStatus, WorkflowOwnerDto, WorkflowOwnersDto } from "../types/panel";
import { Icons } from "../icons/Icons";
import { syncText } from "../plugin-ui/scopeBarText";
import { ContextMenu, useContextMenuState, type ContextMenuItem } from "../components/ContextMenu";
import { contextMenuSeparator } from "../utils/sidebarContextMenuItems";
import { targetRef } from "../ui-targets/registry";
import { useTreeDnd } from "../tree-dnd/useTreeDnd";
import { buildTreeModel, TREE_ROOT, type DropPolicy, type TreeEntry } from "../tree-dnd/treeMove";
import { keepTreeFocus } from "../tree-dnd/treeFocus";
import { useUndoHistoryOptional } from "../navigation/UndoHistoryContext";
import { WorkflowHoverCard } from "./WorkflowHoverCard";
import { buildFolderTree, folderName, folderPaths, joinFolder, movedPath, normalizeFolder, parentFolder, type FolderNode } from "./workflowFolders";

export const LOCAL_OWNER: WorkflowOwnerDto = { id: "local", kind: "local", label: "Local" };

export function ownerName(owner: WorkflowOwnerDto | undefined): string {
  return owner?.kind === "team" ? `Team · ${owner.label}` : "Local";
}

export function OwnerIcon({ owner }: { owner: WorkflowOwnerDto | undefined }) {
  return owner?.kind === "team" ? <Icons.Users /> : <Icons.Monitor />;
}

/** Who can see it, in words (tooltips on the folder and the toolbar chip). */
export function ownerHelp(owner: WorkflowOwnerDto | undefined): string {
  if (owner?.kind !== "team") return "Local workflows stay with your account on this PC.";
  const who = `Team ${owner.label}: every member sees it and changes sync to the team.`;
  return owner.readOnly && owner.reason ? `${who} ${owner.reason}` : who;
}

/** The folder's second line. Local has none; a team line is where its copy stands. */
export function folderStatus(owner: WorkflowOwnerDto, nowMs: number): string {
  if (owner.kind !== "team") return "";
  if (owner.state === "waiting") return "Waiting for team data";
  if (owner.state === "locked") return "Waiting for your data key";
  if (owner.state === "paused") return "Team Private paused";
  const sync = owner.sync || {};
  return syncText({ state: sync.state, pending: sync.pending, syncedAt: sync.syncedAt ?? null } as PluginScopeStatus, nowMs);
}

const TRIGGER_ICONS: Record<string, typeof Icons.Play> = {
  manual: Icons.Play,
  chat: Icons.Chat,
  schedule: Icons.Clock,
  event: Icons.Zap,
  function: Icons.Puzzle,
};

/** What starts it, as an icon (the words are its name and tooltip). */
function TriggerIcon({ row }: { row: AutomationSummaryDto }) {
  const trigger = row.trigger || { kind: "manual", label: "Manual" };
  const teamRun = row.owner?.kind === "team" && (trigger.kind === "schedule" || trigger.kind === "event");
  const here = teamRun ? (row.run_here ? " · runs on this PC" : " · not on this PC") : "";
  const title = trigger.kind === "function" ? "Function: other workflows run it with a Run workflow node" : `Starts from: ${trigger.label}${here}`;
  const Icon = TRIGGER_ICONS[trigger.kind] || Icons.Zap;
  return (
    <span className={`aw-trigger aw-trigger--${trigger.kind}${teamRun && !row.run_here ? " is-elsewhere" : ""}`} role="img" aria-label={trigger.label} title={title}>
      <Icon />
    </span>
  );
}

/** A tree node in the shared drag-and-drop model (tree-dnd). */
type ListNode =
  | { kind: "owner"; owner: WorkflowOwnerDto; path: "" }
  | { kind: "folder"; owner: WorkflowOwnerDto; path: string }
  | { kind: "row"; owner: WorkflowOwnerDto; row: AutomationSummaryDto };
const ownerNodeId = (ownerId: string) => `owner:${ownerId}`;
const folderNodeId = (ownerId: string, path: string) => `wff:${ownerId}::${path}`;
const rowNodeId = (id: string) => `wf:${id}`;
/** Moves still being saved, drawn where they are going (no jump back on a refresh). */
type PendingMoves = { rows: Record<string, string>; folders: Array<{ owner: string; from: string; to: string }> };
type Editing = { owner: string; path: string; mode: "new" | "rename" };
type MenuTarget =
  | { kind: "workflow"; row: AutomationSummaryDto; owner: WorkflowOwnerDto }
  | { kind: "many"; rows: AutomationSummaryDto[] }
  | { kind: "folder"; folder: FolderNode; owner: WorkflowOwnerDto }
  | { kind: "owner"; owner: WorkflowOwnerDto };

type Props = {
  listId: string;
  listRef: Ref<HTMLElement>;
  owners: WorkflowOwnersDto;
  rows: AutomationSummaryDto[];
  activeId: string;
  collapsed: boolean;
  nowMs: number;
  /** Folders made on this PC that hold nothing yet, per owner id. */
  emptyFolders?: Record<string, string[]>;
  onToggleCollapsed: () => void;
  onOpen: (id: string) => void;
  onCreate: (ownerId: string, folder?: string) => void;
  onImportLocal: () => void;
  onAddFolder?: (ownerId: string, path: string) => void;
  /** File a workflow in a folder; resolves once the list shows it there. */
  onMoveWorkflow?: (id: string, ownerId: string, folder: string) => void | Promise<unknown>;
  /** Rename or move a folder; moving it into its parent removes it and keeps the workflows. */
  onMoveFolder?: (ownerId: string, path: string, newPath: string) => void | Promise<unknown>;
  /** Right-click actions on a workflow. */
  onRenameWorkflow?: (id: string, name: string) => void;
  onDuplicateWorkflow?: (id: string) => void;
  onSetEnabled?: (id: string, enabled: boolean) => void;
  onDeleteWorkflow?: (id: string) => void;
  /** Several picked with Ctrl / Shift + click (asks once). */
  onDeleteWorkflows?: (ids: string[]) => void;
  /** Remove a folder and keep its workflows (asks first). */
  onRemoveFolder?: (ownerId: string, path: string) => void;
  /** Copy or move a whole folder (nested folders and workflows) to another owner (asks first). */
  onCopyFolder?: (fromOwnerId: string, path: string, toOwnerId: string, move: boolean, counts: { workflows: number; folders: number }) => void;
  /** Keep a folder (nested folders, every workflow in it) as one template in the New workflow picker. */
  onSaveFolderTemplate?: (ownerId: string, path: string) => void;
  /** No workflows yet: a few ready-made pipelines to start from in one click. */
  featured?: AutomationTemplateDto[];
  onCreateFrom?: (template: AutomationTemplateDto) => void;
  /** Nothing open: keep a New workflow button in view (not only on hover). */
  showNew?: boolean;
};

/** Workflows sidebar: one section per owner (Local, then each team), with folders
 *  inside. Drag a workflow or a folder onto a folder, or onto the owner, to file it;
 *  right-click anything for what you can do with it. */
export function WorkflowList({ listId, listRef, owners, rows, activeId, collapsed, nowMs, emptyFolders = {}, onToggleCollapsed, onOpen, onCreate, onImportLocal, onAddFolder, onMoveWorkflow, onMoveFolder, onRenameWorkflow, onDuplicateWorkflow, onSetEnabled, onDeleteWorkflow, onDeleteWorkflows, onRemoveFolder, onCopyFolder, onSaveFolderTemplate, featured = [], onCreateFrom, showNew }: Props) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<Editing | null>(null);
  const [renamingId, setRenamingId] = useState("");
  const [pending, setPending] = useState<PendingMoves>({ rows: {}, folders: [] });
  const undoHistory = useUndoHistoryOptional();
  const { menu, open: openMenu, close: closeMenu } = useContextMenuState<MenuTarget>();
  const sections = owners.owners?.length ? owners.owners : [LOCAL_OWNER];
  // No workflows anywhere: a "Make your first workflow" card that creates in the first writable section.
  const heroOwner = sections.find((owner) => !owner.readOnly);
  const heroShown = !rows.length && !!heroOwner;
  const canFile = !!onMoveWorkflow;
  // Several workflows picked with Ctrl + click (one more) or Shift + click (a run of them),
  // like the Duckies list: right-click, drag or Delete acts on all of them.
  const [pickedIds, setPicked] = useState<string[]>([]);
  const [syncing, setSyncing] = useState("");
  const updateOnline = async (teamId: string) => {
    setSyncing(teamId);
    try {
      await getApi()?.workflow_sync?.(true, teamId, true);
    } finally {
      setSyncing((current) => (current === teamId ? "" : current));
    }
  };
  const picked = pickedIds.filter((id) => rows.some((row) => row.id === id));
  const anchor = useRef("");
  const order: string[] = [];  // visible rows, top to bottom (filled while rendering)
  const rowOwner = (row: AutomationSummaryDto) => sections.find((owner) => owner.id === (row.owner?.id || LOCAL_OWNER.id)) || LOCAL_OWNER;
  const pickedRows = () => rows.filter((row) => picked.includes(row.id));
  const clickRow = (event: MouseEvent, row: AutomationSummaryDto) => {
    if (event.ctrlKey || event.metaKey) {
      const base = picked.length ? picked : activeId && order.includes(activeId) ? [activeId] : [];
      setPicked(base.includes(row.id) ? base.filter((id) => id !== row.id) : [...base, row.id]);
      anchor.current = row.id;
      return;
    }
    if (event.shiftKey) {
      const from = order.indexOf(anchor.current || activeId), to = order.indexOf(row.id);
      if (from >= 0 && to >= 0) { setPicked(order.slice(Math.min(from, to), Math.max(from, to) + 1)); return; }
    }
    setPicked([]);
    anchor.current = row.id;
    onOpen(row.id);
  };
  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (event.target instanceof Element && event.target.closest("input, textarea, [contenteditable]:not([contenteditable='false'])")) return;
    if (event.key === "Escape" && picked.length) { event.stopPropagation(); setPicked([]); return; }
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    const focused = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>("[data-aw-row]")?.dataset.awRow : "";
    const ids = (picked.length ? pickedRows() : rows.filter((row) => row.id === focused)).filter((row) => !rowOwner(row).readOnly).map((row) => row.id);
    if (!ids.length) return;
    event.preventDefault();
    event.stopPropagation();
    if (ids.length > 1 && onDeleteWorkflows) onDeleteWorkflows(ids);
    else onDeleteWorkflow?.(ids[0]);
  };

  // Moves still being saved show at their destination right away.
  const shownRows = useMemo(() => {
    if (!Object.keys(pending.rows).length && !pending.folders.length) return rows;
    return rows.map((row) => {
      const ownerId = row.owner?.id || LOCAL_OWNER.id;
      let folder = pending.rows[row.id] ?? normalizeFolder(row.folder);
      for (const move of pending.folders) {
        if (move.owner !== ownerId) continue;
        const moved = movedPath(folder, move.from, move.to);
        if (moved !== null) folder = moved;
      }
      return folder === normalizeFolder(row.folder) ? row : { ...row, folder };
    });
  }, [rows, pending]);
  const shownEmptyFolders = useMemo(() => {
    if (!pending.folders.length) return emptyFolders;
    const out: Record<string, string[]> = { ...emptyFolders };
    for (const move of pending.folders) {
      const list = (out[move.owner] || []).map((path) => movedPath(path, move.from, move.to) ?? path);
      out[move.owner] = [...new Set([...list, move.to])];
    }
    return out;
  }, [emptyFolders, pending.folders]);

  // ── Drag and drop: the shared tree engine (tree-dnd). Workflows are sorted by name,
  // so a drop only picks the folder (or the owner's top level).
  const ownerTrees = useMemo(
    () => sections.map((owner) => {
      const mine = shownRows.filter((row) => (row.owner?.id || LOCAL_OWNER.id) === owner.id);
      return { owner, mine, tree: buildFolderTree(mine, shownEmptyFolders[owner.id] || []) };
    }),
    [sections, shownRows, shownEmptyFolders],
  );
  const { treeModel, nodes } = useMemo(() => {
    const entries: TreeEntry[] = [];
    const info = new Map<string, ListNode>();
    for (const { owner, tree } of ownerTrees) {
      const ownerId = ownerNodeId(owner.id);
      entries.push({ id: ownerId, parentId: TREE_ROOT, branch: true });
      info.set(ownerId, { kind: "owner", owner, path: "" });
      const walk = (node: FolderNode, parentId: string, depth: number) => {
        for (const child of node.folders) {
          const id = folderNodeId(owner.id, child.path);
          entries.push({ id, parentId, branch: true });
          info.set(id, { kind: "folder", owner, path: child.path });
          if (depth < 64) walk(child, id, depth + 1);
        }
        for (const row of node.rows) {
          const id = rowNodeId(row.id);
          entries.push({ id, parentId, branch: false });
          info.set(id, { kind: "row", owner, row });
        }
      };
      walk(tree, ownerId, 0);
    }
    return { treeModel: buildTreeModel(entries), nodes: info };
  }, [ownerTrees]);
  const treeRef = useRef({ treeModel, nodes });
  treeRef.current = { treeModel, nodes };
  const closedRef = useRef(closed);
  closedRef.current = closed;
  const dropPolicy: DropPolicy = {
    ordered: false,
    intoOnly: (id) => nodes.get(id)?.kind === "owner",
    isExpanded: (id) => {
      const node = treeRef.current.nodes.get(id);
      if (!node || node.kind === "row") return true;
      return !closedRef.current[node.kind === "owner" ? node.owner.id : `${node.owner.id}:${node.path}`];
    },
    // Only into a writable owner's folders, and never into another owner (that asks
    // first, from the folder's menu).
    acceptsDrop: (parentId, sources) => {
      const parent = treeRef.current.nodes.get(parentId);
      if (!parent || parent.kind === "row" || parent.owner.readOnly) return false;
      return sources.every((source) => treeRef.current.nodes.get(source)?.owner.id === parent.owner.id);
    },
  };
  const dropPolicyRef = useRef(dropPolicy);
  dropPolicyRef.current = dropPolicy;

  const settle = (work: void | Promise<unknown>, clear: () => void) => {
    void Promise.resolve(work).catch(() => undefined).finally(clear);
  };
  /** File workflows (shown at once), with Ctrl+Z / Ctrl+Y in the Workflows history. */
  const moveRows = (moves: Array<{ id: string; owner: string; from: string; to: string }>, record = true) => {
    if (!onMoveWorkflow || !moves.length) return Promise.resolve();
    setPending((current) => ({ ...current, rows: { ...current.rows, ...Object.fromEntries(moves.map((move) => [move.id, move.to])) } }));
    const work = Promise.all(moves.map((move) => Promise.resolve(onMoveWorkflow(move.id, move.owner, move.to))));
    settle(work, () => setPending((current) => {
      const next = { ...current.rows };
      for (const move of moves) if (next[move.id] === move.to) delete next[move.id];
      return { ...current, rows: next };
    }));
    if (record && undoHistory) {
      undoHistory.push("workflows", {
        label: moves.length > 1 ? `Move ${moves.length} workflows` : "Move workflow",
        undo: () => moveRows(moves.map((move) => ({ ...move, from: move.to, to: move.from })), false).then(() => undefined),
        redo: () => moveRows(moves, false).then(() => undefined),
      });
    }
    return work.then(() => undefined);
  };
  const moveFolderTo = (owner: string, from: string, to: string, record = true) => {
    if (!onMoveFolder || from === to) return Promise.resolve();
    const entry = { owner, from, to };
    setPending((current) => ({ ...current, folders: [...current.folders, entry] }));
    const work = Promise.resolve(onMoveFolder(owner, from, to));
    settle(work, () => setPending((current) => ({ ...current, folders: current.folders.filter((item) => item !== entry) })));
    if (record && undoHistory) {
      undoHistory.push("workflows", {
        label: `Move ${folderName(from)}`,
        undo: () => moveFolderTo(owner, to, from, false),
        redo: () => moveFolderTo(owner, from, to, false),
      });
    }
    return work.then(() => undefined);
  };

  const dnd = useTreeDnd({
    getModel: () => treeRef.current.treeModel,
    getPolicy: () => dropPolicyRef.current,
    dragSources: (id) => {
      const node = treeRef.current.nodes.get(id);
      if (node?.kind !== "row" || !picked.includes(node.row.id)) return [id];
      return pickedRows().filter((row) => rowOwner(row).id === node.owner.id).map((row) => rowNodeId(row.id));
    },
    canDrag: (id) => {
      const node = treeRef.current.nodes.get(id);
      if (!node || node.kind === "owner" || node.owner.readOnly) return false;
      if (node.kind === "row") return canFile;
      return !!onMoveFolder && !(editing?.mode === "rename" && editing.owner === node.owner.id && editing.path === node.path);
    },
    dragData: (sources) => (treeRef.current.nodes.get(sources[0])?.kind === "folder" ? "folder" : "workflow"),
    labelFor: (id) => {
      const node = treeRef.current.nodes.get(id);
      if (!node) return "";
      return node.kind === "row" ? node.row.name || "Untitled" : node.kind === "folder" ? folderName(node.path) : ownerName(node.owner);
    },
    expand: (id) => {
      const node = treeRef.current.nodes.get(id);
      if (!node || node.kind === "row") return;
      const key = node.kind === "owner" ? node.owner.id : `${node.owner.id}:${node.path}`;
      setClosed((current) => ({ ...current, [key]: false }));
    },
    onDrop: ({ sources, target }) => {
      const dest = treeRef.current.nodes.get(target.parentId);
      if (!dest || dest.kind === "row") return;
      const rowMoves: Array<{ id: string; owner: string; from: string; to: string }> = [];
      for (const source of sources) {
        const node = treeRef.current.nodes.get(source);
        if (!node || node.owner.id !== dest.owner.id) continue;
        if (node.kind === "row") {
          const from = normalizeFolder(node.row.folder);
          if (from !== dest.path) rowMoves.push({ id: node.row.id, owner: dest.owner.id, from, to: dest.path });
        } else if (node.kind === "folder") {
          void moveFolderTo(dest.owner.id, node.path, joinFolder(dest.path, folderName(node.path)));
        }
      }
      void moveRows(rowMoves);
      keepTreeFocus("workflows");
    },
  });

  const commitName = (owner: WorkflowOwnerDto, name: string) => {
    const edit = editing;
    setEditing(null);
    const clean = normalizeFolder(name).replace(/\//g, " ");
    if (!edit || !clean) return;
    if (edit.mode === "new") onAddFolder?.(owner.id, joinFolder(edit.path, clean));
    else if (clean !== folderName(edit.path)) onMoveFolder?.(owner.id, edit.path, joinFolder(parentFolder(edit.path), clean));
  };
  const newFolderIn = (owner: WorkflowOwnerDto, path: string) => {
    setClosed((current) => ({ ...current, [path ? `${owner.id}:${path}` : owner.id]: false }));
    setEditing({ owner: owner.id, path, mode: "new" });
  };

  const menuItems = (target: MenuTarget): ContextMenuItem[] => {
    if (target.kind === "many") {
      const editable = target.rows.filter((row) => !rowOwner(row).readOnly);
      const n = target.rows.length;
      const filed = editable.filter((row) => normalizeFolder(row.folder));
      return [
        ...(onDuplicateWorkflow ? [{ id: "duplicate", label: `Duplicate ${n}`, onClick: () => target.rows.forEach((row) => onDuplicateWorkflow(row.id)) }] : []),
        ...(onSetEnabled && editable.some((row) => !row.enabled) ? [{ id: "on", label: `Turn on ${editable.filter((row) => !row.enabled).length}`, onClick: () => editable.filter((row) => !row.enabled).forEach((row) => onSetEnabled(row.id, true)) }] : []),
        ...(onSetEnabled && editable.some((row) => row.enabled) ? [{ id: "off", label: `Turn off ${editable.filter((row) => row.enabled).length}`, onClick: () => editable.filter((row) => row.enabled).forEach((row) => onSetEnabled(row.id, false)) }] : []),
        ...(onMoveWorkflow && filed.length ? [{ id: "unfile", label: "Move out of their folders", onClick: () => filed.forEach((row) => onMoveWorkflow(row.id, rowOwner(row).id, parentFolder(normalizeFolder(row.folder)))) }] : []),
        ...(editable.length && (onDeleteWorkflows || onDeleteWorkflow) ? [contextMenuSeparator("sep-delete"), { id: "delete", label: `Delete ${editable.length} workflow${editable.length === 1 ? "" : "s"}`, danger: true, onClick: () => onDeleteWorkflows ? onDeleteWorkflows(editable.map((row) => row.id)) : onDeleteWorkflow?.(editable[0].id) }] : []),
      ];
    }
    const owner = target.owner;
    const editable = !owner.readOnly;
    if (target.kind === "workflow") {
      const { row } = target;
      const folder = normalizeFolder(row.folder);
      return [
        ...(row.id !== activeId ? [{ id: "open", label: "Open", onClick: () => onOpen(row.id) }] : []),
        ...(editable && onRenameWorkflow ? [{ id: "rename", label: "Rename", onClick: () => setRenamingId(row.id) }] : []),
        ...(onDuplicateWorkflow ? [{ id: "duplicate", label: editable ? "Duplicate" : "Duplicate to Local", onClick: () => onDuplicateWorkflow(row.id) }] : []),
        ...(editable && onSetEnabled ? [contextMenuSeparator("sep-on"), { id: "enabled", label: row.enabled ? "On" : "Off", switch: true, checked: !!row.enabled, onClick: () => onSetEnabled(row.id, !row.enabled) }] : []),
        ...(editable && folder && onMoveWorkflow ? [{ id: "unfile", label: "Move out of folder", onClick: () => onMoveWorkflow(row.id, owner.id, parentFolder(folder)) }] : []),
        ...(editable && onDeleteWorkflow ? [contextMenuSeparator("sep-delete"), { id: "delete", label: "Delete", danger: true, onClick: () => onDeleteWorkflow(row.id) }] : []),
      ];
    }
    if (target.kind === "folder") {
      const { folder } = target;
      // Another owner's copy of the whole tree: Local from a team, a team from Local or another team.
      const others = onCopyFolder ? sections.filter((to) => to.id !== owner.id && !to.readOnly) : [];
      const counts = { workflows: folder.count, folders: folderPaths(folder).length };
      const send: ContextMenuItem[] = others.length ? [
        contextMenuSeparator("sep-send"),
        ...others.map((to) => ({ id: `copy-${to.id}`, label: `Copy folder to ${ownerName(to)}`, onClick: () => onCopyFolder?.(owner.id, folder.path, to.id, false, counts) })),
        ...(editable ? others.map((to) => ({ id: `move-${to.id}`, label: `Move folder to ${ownerName(to)}`, onClick: () => onCopyFolder?.(owner.id, folder.path, to.id, true, counts) })) : []),
      ] : [];
      const keep: ContextMenuItem[] = onSaveFolderTemplate && folder.count
        ? [{ id: "save-template", label: "Save folder as template", onClick: () => onSaveFolderTemplate(owner.id, folder.path) }]
        : [];
      if (!editable) return [{ id: "none", label: "Read-only", disabled: true }, ...send, ...keep];
      return [
        { id: "new", label: "New workflow here", onClick: () => onCreate(owner.id, folder.path) },
        ...(onAddFolder ? [{ id: "new-folder", label: "New folder inside", onClick: () => newFolderIn(owner, folder.path) }] : []),
        ...(onMoveFolder ? [{ id: "rename", label: "Rename", onClick: () => setEditing({ owner: owner.id, path: folder.path, mode: "rename" }) }] : []),
        ...send,
        ...keep,
        ...(onMoveFolder ? [
          contextMenuSeparator("sep-remove"),
          { id: "remove", label: "Remove folder (keeps its workflows)", danger: true, onClick: () => onRemoveFolder ? onRemoveFolder(owner.id, folder.path) : onMoveFolder(owner.id, folder.path, parentFolder(folder.path)) },
        ] : []),
      ];
    }
    return [
      ...(editable ? [
        { id: "new", label: "New workflow", onClick: () => onCreate(owner.id) },
        ...(onAddFolder ? [{ id: "new-folder", label: "New folder", onClick: () => newFolderIn(owner, "") }] : []),
      ] : [{ id: "none", label: "Read-only", disabled: true }]),
      ...(owner.kind === "team" ? [contextMenuSeparator("sep-sync"), { id: "sync", label: "Update online", onClick: () => void updateOnline(owner.id) }] : []),
    ];
  };
  const contextFor = (target: MenuTarget) => (event: MouseEvent) => {
    if (target.kind === "workflow" && picked.length > 1 && picked.includes(target.row.id)) { openMenu(event, { kind: "many", rows: pickedRows() }); return; }
    if (target.kind === "workflow" && picked.length && !picked.includes(target.row.id)) setPicked([]);
    openMenu(event, target);
  };

  const renderRows = (owner: WorkflowOwnerDto, list: AutomationSummaryDto[], depth: number, visible: boolean) => list.map((row) => {
    if (visible) order.push(row.id);
    return (
    <li key={row.id} data-tree-node={rowNodeId(row.id)}>
      {renamingId === row.id ? (
        <div className="aw-tree-folder aw-tree-folder--editing" style={{ "--aw-level": depth + 1 } as CSSProperties}>
          <NameInput label="Rename workflow" initial={row.name || ""} placeholder="Workflow name" maxLength={120}
            onDone={(name) => { setRenamingId(""); const clean = name.trim(); if (clean && clean !== row.name) onRenameWorkflow?.(row.id, clean); }}
            onCancel={() => setRenamingId("")} />
        </div>
      ) : (
        <WorkflowHoverCard row={row} icon={<TriggerIcon row={row} />} onOpen={() => { setPicked([]); onOpen(row.id); }}>
        <button type="button" ref={targetRef(`workflows.list.row.${row.id}`, { route: "workflows", label: row.name || "Untitled" })} data-aw-row={row.id} data-tree-row={rowNodeId(row.id)}
          className={"aw-list-row" + (row.id === activeId ? " is-active" : "") + (picked.includes(row.id) ? " is-picked" : "")} aria-current={row.id === activeId ? "true" : undefined} aria-selected={picked.length ? picked.includes(row.id) : undefined}
          style={{ "--aw-level": depth + 1 } as CSSProperties} draggable={canFile && !owner.readOnly}
          onClick={(event) => clickRow(event, row)} onContextMenu={contextFor({ kind: "workflow", row, owner })}>
          <span className="aw-list-name">{row.name || "Untitled"}</span>
          {/* Laid over the end of the name, so the name gets the whole row. */}
          <span className="aw-row-end">
            <TriggerIcon row={row} />
            <span className={`aw-list-state${row.enabled ? " is-on" : ""}`} role="img" aria-label={row.enabled ? "On" : "Off"} title={row.enabled ? "On: it runs when started" : "Off: it won't run"} />
          </span>
        </button>
        </WorkflowHoverCard>
      )}
    </li>
    );
  });

  const newFolderRow = (owner: WorkflowOwnerDto, parent: string, depth: number) => editing?.mode === "new" && editing.owner === owner.id && editing.path === parent ? (
    <li className="aw-tree-folder aw-tree-folder--editing" style={{ "--aw-level": depth + 1 } as CSSProperties}>
      <span className="aw-folder-icon" aria-hidden="true"><Icons.Folder /></span>
      <NameInput label="New folder name" initial="" onDone={(name) => commitName(owner, name)} onCancel={() => setEditing(null)} />
    </li>
  ) : null;

  const renderFolder = (owner: WorkflowOwnerDto, folder: FolderNode, depth: number, visible: boolean) => {
    const key = `${owner.id}:${folder.path}`;
    const open = !closed[key];
    const renaming = editing?.mode === "rename" && editing.owner === owner.id && editing.path === folder.path;
    const bodyId = `${listId}-${owner.id}-f-${folder.path}`;
    return (
      <li key={folder.path} className="aw-tree-item" data-tree-node={folderNodeId(owner.id, folder.path)}>
        <div className="aw-tree-folder" style={{ "--aw-level": depth + 1 } as CSSProperties} data-tree-row={folderNodeId(owner.id, folder.path)}
          draggable={!!onMoveFolder && !owner.readOnly && !renaming}
          onContextMenu={renaming ? undefined : contextFor({ kind: "folder", folder, owner })}>
          {renaming ? (
            <>
              <span className="aw-folder-icon" aria-hidden="true"><Icons.Folder /></span>
              <NameInput label="Folder name" initial={folder.name} onDone={(name) => commitName(owner, name)} onCancel={() => setEditing(null)} />
            </>
          ) : (
            <button type="button" className="aw-tree-toggle" aria-expanded={open} aria-controls={bodyId} aria-label={`Folder ${folder.name}`} title={folder.path}
              onClick={() => setClosed((current) => ({ ...current, [key]: open }))}>
              <span className="aw-accordion-chevron" aria-hidden="true"><Icons.ChevronDown /></span>
              <span className="aw-folder-icon" aria-hidden="true"><Icons.Folder /></span>
              <span className="aw-list-name">{folder.name}</span><small>{folder.count}</small>
            </button>
          )}
          {!owner.readOnly && !renaming ? (
            <span className="aw-tree-actions">
              <button type="button" className="aw-icon-button" title={`New workflow in ${folder.name}`} aria-label={`New workflow in ${folder.name}`} onClick={() => onCreate(owner.id, folder.path)}><Icons.Plus /></button>
              {onAddFolder ? <button type="button" className="aw-icon-button" title="New folder inside" aria-label={`New folder in ${folder.name}`} onClick={() => newFolderIn(owner, folder.path)}><Icons.FolderPlus /></button> : null}
              {onMoveFolder ? <button type="button" className="aw-icon-button" title="Rename folder" aria-label={`Rename ${folder.name}`} onClick={() => setEditing({ owner: owner.id, path: folder.path, mode: "rename" })}><Icons.Pencil /></button> : null}
              {onMoveFolder ? <button type="button" className="aw-icon-button" title="Remove folder (its workflows move up a level)" aria-label={`Remove folder ${folder.name}`} onClick={() => onRemoveFolder ? onRemoveFolder(owner.id, folder.path) : onMoveFolder(owner.id, folder.path, parentFolder(folder.path))}><Icons.Trash /></button> : null}
            </span>
          ) : null}
        </div>
        {/* --aw-level places this level's guide line under the folder's chevron. */}
        <ul className="aw-list-ul aw-tree-children" id={bodyId} hidden={!open} style={{ "--aw-level": depth + 1 } as CSSProperties}>
          {newFolderRow(owner, folder.path, depth + 1)}
          {folder.folders.map((child) => renderFolder(owner, child, depth + 1, visible && open))}
          {renderRows(owner, folder.rows, depth + 1, visible && open)}
          {!folder.count && !folder.folders.length ? <li className="aw-section-empty" style={{ "--aw-level": depth + 2 } as CSSProperties}>Empty. Drag workflows here.</li> : null}
        </ul>
      </li>
    );
  };

  return (
    <aside className="aw-list no-drag" ref={listRef} aria-label="Workflows" data-aw-zoom="list" data-undo-scope="workflows" tabIndex={-1} onKeyDown={onListKeyDown}>
      <div className="aw-list-head">
        <button type="button" ref={targetRef("workflows.list.toggle", { route: "workflows", label: "Fold or unfold the Workflows list" })} className="aw-list-toggle" title={collapsed ? "Expand Workflows" : "Collapse Workflows"} aria-expanded={!collapsed} aria-controls={listId} onClick={onToggleCollapsed}>
          <span className="aw-list-title"><span className="aw-list-icon" aria-hidden="true"><Icons.Workflow /></span><strong>Workflows</strong></span>
          <span className="aw-accordion-chevron" aria-hidden="true"><Icons.ChevronDown /></span>
        </button>
      </div>
      <div className="aw-list-sections" id={listId} hidden={collapsed} {...dnd.rootProps}>
        {heroShown ? (
          <div ref={targetRef("workflows.empty.hero", { route: "workflows", label: "Make your first workflow" })} className="aw-empty-hero" role="region" aria-label="Make your first workflow">
            <span className="aw-empty-hero-icon" aria-hidden="true"><Icons.Workflow /></span>
            <strong>Make your first workflow</strong>
            <p>Start from a ready-made pipeline, or from a blank canvas.</p>
            <button ref={targetRef("workflows.empty.new", { route: "workflows", label: "New workflow" })} type="button" className="aw-new-button" onClick={() => onCreate(heroOwner!.id)}><Icons.Plus /> New workflow</button>
            {featured.length && onCreateFrom ? (
              <div ref={targetRef("workflows.empty.picks", { route: "workflows", label: "Start from a template" })} className="aw-empty-picks" role="group" aria-label="Start from a template">
                {featured.map((template) => (
                  <button key={template.id} type="button" className="aw-empty-pick" title={template.description || template.name} onClick={() => onCreateFrom(template)}>
                    <span className="aw-empty-pick-icon" aria-hidden="true">{template.icon || "⚡"}</span>
                    <span className="aw-empty-pick-text"><span>{template.name}</span><small>{template.category || "Template"}</small></span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {ownerTrees.map(({ owner, mine, tree }) => {
          const open = !closed[owner.id];
          const name = owner.kind === "team" ? owner.label : "Local";
          const bodyId = `${listId}-${owner.id}`;
          return (
            <section className={`aw-workflow-section aw-folder aw-folder--${owner.kind}`} key={owner.id} aria-label={ownerName(owner)} data-tree-node={ownerNodeId(owner.id)}>
              <div className="aw-section-head" data-tree-row={ownerNodeId(owner.id)} onContextMenu={contextFor({ kind: "owner", owner })}>
                <button type="button" className="aw-section-toggle" aria-label={ownerName(owner)} aria-expanded={open} aria-controls={bodyId} title={[ownerHelp(owner), folderStatus(owner, nowMs)].filter(Boolean).join("\n")} onClick={() => setClosed((current) => ({ ...current, [owner.id]: open }))}>
                  <span className="aw-accordion-chevron" aria-hidden="true"><Icons.ChevronDown /></span>
                  <span className="aw-folder-icon" aria-hidden="true"><OwnerIcon owner={owner} /></span>
                  <strong>{name}</strong><small>{mine.length}</small>
                </button>
                {owner.sync?.error ? <span className="aw-folder-lock aw-sync-error" role="img" aria-label={`Sync problem: ${owner.sync.error}`} title={owner.sync.error}><Icons.AlertTriangle /></span> : null}
                {owner.readOnly ? <span className="aw-folder-lock" role="img" aria-label={`Read-only: ${owner.reason || ""}`} title={owner.reason}><Icons.Lock /></span> : null}
                {/* Shown on hover, over the end of the row, like a folder's. */}
                {(owner.kind === "team" && owner.slug) || !owner.readOnly ? <span className="aw-tree-actions aw-section-actions">
                  {owner.kind === "team" ? <button type="button" className="aw-icon-button" title={`Update ${ownerName(owner)} online`} aria-label={`Update ${ownerName(owner)} online`} disabled={syncing === owner.id} onClick={() => void updateOnline(owner.id)}>{syncing === owner.id ? <Icons.Spinner /> : <Icons.Refresh />}</button> : null}
                  {!owner.readOnly && onAddFolder ? <button type="button" className="aw-icon-button" title={`New folder in ${ownerName(owner)}`} aria-label={`New folder in ${ownerName(owner)}`} onClick={() => newFolderIn(owner, "")}><Icons.FolderPlus /></button> : null}
                  {!owner.readOnly ? <button ref={targetRef(`workflows.list.new.${owner.id}`, { route: "workflows", label: `New workflow in ${ownerName(owner)}` })} type="button" className="aw-icon-button" title={`New workflow in ${ownerName(owner)}`} aria-label={`New workflow in ${ownerName(owner)}`} onClick={() => onCreate(owner.id)}><Icons.Plus /></button> : null}
                </span> : null}
              </div>
              <ul className="aw-list-ul" id={bodyId} hidden={!open}>
                {newFolderRow(owner, "", 0)}
                {tree.folders.map((folder) => renderFolder(owner, folder, 0, open && !collapsed))}
                {renderRows(owner, tree.rows, 0, open && !collapsed)}
                {!mine.length && !tree.folders.length && !(editing?.owner === owner.id) ? (
                  // The hero above already has the big New workflow for this section.
                  owner.readOnly || (heroShown && owner.id === heroOwner?.id)
                    ? <li className="aw-section-empty" style={{ "--aw-level": 1 } as CSSProperties}>No workflows yet</li>
                    : (
                      <li className="aw-section-empty aw-section-empty--card">
                        <span className="aw-section-empty-icon" aria-hidden="true"><OwnerIcon owner={owner} /></span>
                        <strong>No workflows yet</strong>
                        <small>{owner.kind === "team" ? `Shared with everyone in ${owner.label}.` : "Saved on this PC."}</small>
                        <button ref={targetRef(`workflows.section.new.${owner.id}`, { route: "workflows", label: `New workflow in ${ownerName(owner)}` })} type="button" className="aw-new-button aw-section-new" onClick={() => onCreate(owner.id)}><Icons.Plus /> New workflow</button>
                      </li>
                    )
                ) : null}
                {owner.kind === "local" && owners.localImport ? (
                  <li className="aw-import-row">
                    <span>{owners.localImport} from when you were signed out</span>
                    <button type="button" onClick={onImportLocal}><Icons.Download /> Bring in</button>
                  </li>
                ) : null}
              </ul>
            </section>
          );
        })}
        {owners.signedIn === false ? <p className="aw-list-hint">Sign in to share workflows with a team.</p> : null}
      </div>
      {showNew && rows.length && !collapsed && sections.some((owner) => !owner.readOnly) ? (
        <div className="aw-list-foot">
          <button ref={targetRef("workflows.list.new", { route: "workflows", label: "New workflow" })} type="button" className="aw-new-button" onClick={() => onCreate(sections.find((owner) => !owner.readOnly)!.id)}><Icons.Plus /> New workflow</button>
        </div>
      ) : null}
      {menu ? <ContextMenu x={menu.x} y={menu.y} onClose={closeMenu} items={menuItems(menu.data)} /> : null}
    </aside>
  );
}

/** Enter or blur saves, Escape cancels. */
function NameInput({ label, initial, placeholder = "Folder name", maxLength = 64, onDone, onCancel }: { label: string; initial: string; placeholder?: string; maxLength?: number; onDone: (name: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  const input = useRef<HTMLInputElement>(null);
  const finished = useRef(false);
  useEffect(() => { input.current?.focus({ preventScroll: true }); input.current?.select(); }, []);
  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    if (save) onDone(text); else onCancel();
  };
  return <input ref={input} className="aw-tree-input" aria-label={label} value={text} placeholder={placeholder} maxLength={maxLength}
    onChange={(event) => setText(event.target.value)} onBlur={() => finish(true)}
    onKeyDown={(event) => {
      event.stopPropagation();
      if (event.nativeEvent.isComposing) return;
      if (event.key === "Enter") { event.preventDefault(); finish(true); }
      else if (event.key === "Escape") { event.preventDefault(); finish(false); }
    }} />;
}

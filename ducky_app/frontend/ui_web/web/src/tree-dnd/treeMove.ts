/**
 * Pure tree moves shared by every sidebar tree (Duckies, Content, Workflows).
 *
 * A tree is a map of ordered child ids per parent. A move takes whole nodes, so a
 * folder or group always travels with everything inside it, and a node can never be
 * dropped into itself or anything below it: the hierarchy cannot break.
 */

/** Parent id of top-level nodes. */
export const TREE_ROOT = "";

export interface TreeEntry {
  id: string;
  /** TREE_ROOT for a top-level node. */
  parentId: string;
  /** Holds children: a folder, group or directory. */
  branch: boolean;
}

export interface TreeModel {
  /** Ordered child ids per parent id (TREE_ROOT for the top level). */
  readonly children: ReadonlyMap<string, readonly string[]>;
  readonly parent: ReadonlyMap<string, string>;
  readonly branches: ReadonlySet<string>;
}

/** Entries in display order (parents need not come before their children). */
export function buildTreeModel(entries: readonly TreeEntry[]): TreeModel {
  const children = new Map<string, string[]>();
  const parent = new Map<string, string>();
  const branches = new Set<string>();
  for (const entry of entries) {
    if (parent.has(entry.id)) continue;
    parent.set(entry.id, entry.parentId);
    if (entry.branch) branches.add(entry.id);
    const list = children.get(entry.parentId);
    if (list) list.push(entry.id);
    else children.set(entry.parentId, [entry.id]);
  }
  return { children, parent, branches };
}

export function hasNode(model: TreeModel, id: string): boolean {
  return model.parent.has(id);
}

export function childIds(model: TreeModel, parentId: string): readonly string[] {
  return model.children.get(parentId) ?? [];
}

export function isBranch(model: TreeModel, id: string): boolean {
  return model.branches.has(id);
}

/** Where a node sits now, or null when the tree does not have it. */
export function locate(model: TreeModel, id: string): { parentId: string; index: number } | null {
  const parentId = model.parent.get(id);
  if (parentId === undefined) return null;
  return { parentId, index: childIds(model, parentId).indexOf(id) };
}

/** True when `id` is `ancestorId` or sits anywhere below it. */
export function isSelfOrDescendant(model: TreeModel, ancestorId: string, id: string): boolean {
  let current: string | undefined = id;
  const seen = new Set<string>();
  while (current !== undefined && current !== TREE_ROOT && !seen.has(current)) {
    if (current === ancestorId) return true;
    seen.add(current);
    current = model.parent.get(current);
  }
  return false;
}

/** Every node, parents before children, in display order. */
export function treeOrder(model: TreeModel, from = TREE_ROOT): string[] {
  const out: string[] = [];
  const walk = (parentId: string, depth: number) => {
    if (depth > 64) return;
    for (const id of childIds(model, parentId)) {
      out.push(id);
      walk(id, depth + 1);
    }
  };
  walk(from, 0);
  return out;
}

/**
 * The nodes that actually move: known ids, no duplicates, and no node whose ancestor is
 * also moving (it travels inside that ancestor). Display order is kept.
 */
export function topLevelSources(model: TreeModel, ids: readonly string[]): string[] {
  const wanted = new Set(ids.filter((id) => hasNode(model, id)));
  if (!wanted.size) return [];
  const order = treeOrder(model);
  const rank = new Map(order.map((id, index) => [id, index]));
  const kept = [...wanted].filter((id) => {
    let parentId = model.parent.get(id);
    const seen = new Set<string>();
    while (parentId !== undefined && parentId !== TREE_ROOT && !seen.has(parentId)) {
      if (wanted.has(parentId)) return false;
      seen.add(parentId);
      parentId = model.parent.get(parentId);
    }
    return true;
  });
  return kept.sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0));
}

/** before / after a row, or into a branch row. */
export type DropZone = "before" | "after" | "into";

export interface DropPolicy {
  /** Children keep the order people give them (Duckies). False: the host sorts them
   *  (files, workflows) and only the parent counts, like the VS Code explorer. */
  ordered: boolean;
  /** Leaves always sit above branches inside a parent (Duckies: chats above folders). */
  leavesFirst?: boolean;
  /** A branch whose children show. Its "after" edge is the slot above its first child. */
  isExpanded?: (id: string) => boolean;
  /** Rows that only take drops into them (a project or owner row). */
  intoOnly?: (id: string) => boolean;
  /** Whether this parent takes these nodes (read-only folders, another owner…). */
  acceptsDrop?: (parentId: string, sources: readonly string[]) => boolean;
  /** Where a drop on blank space lands. Defaults to TREE_ROOT. */
  blankParent?: string;
}

/** Which part of a row the pointer is on. */
export function zoneForPointer(
  offsetY: number,
  height: number,
  branch: boolean,
  policy: Pick<DropPolicy, "ordered">,
  intoOnly = false,
): DropZone {
  if (intoOnly) return "into";
  if (!policy.ordered) return branch ? "into" : "before";
  if (!(height > 0)) return branch ? "into" : "before";
  const y = Math.min(Math.max(offsetY, 0), height);
  if (branch) {
    if (y < height * 0.25) return "before";
    if (y > height * 0.75) return "after";
    return "into";
  }
  return y < height / 2 ? "before" : "after";
}

/**
 * Where the moving nodes land. `index` counts the parent's children once the moving
 * nodes have left it (null: the host sorts). `into` marks a drop onto a branch row.
 */
export interface DropTarget {
  parentId: string;
  index: number | null;
  into: boolean;
}

function remainingChildren(model: TreeModel, parentId: string, moving: ReadonlySet<string>): string[] {
  return childIds(model, parentId).filter((id) => !moving.has(id));
}

/** Index in the list without the moving nodes for a slot in the full list. */
function remainingIndex(model: TreeModel, parentId: string, fullIndex: number, moving: ReadonlySet<string>): number {
  let index = 0;
  const list = childIds(model, parentId);
  for (let i = 0; i < Math.min(fullIndex, list.length); i += 1) {
    if (!moving.has(list[i])) index += 1;
  }
  return index;
}

function leafCount(model: TreeModel, list: readonly string[]): number {
  return list.filter((id) => !isBranch(model, id)).length;
}

/** Keep leaves above branches: a leaf never lands among branches, nor a branch among leaves. */
function clampForKind(model: TreeModel, remaining: readonly string[], index: number, movingBranch: boolean): number {
  const leaves = leafCount(model, remaining);
  return movingBranch ? Math.max(index, leaves) : Math.min(index, leaves);
}

/** True when this drop would leave every moving node where it is. */
export function isNoopDrop(model: TreeModel, sources: readonly string[], target: DropTarget): boolean {
  if (!sources.length) return true;
  if (sources.some((id) => model.parent.get(id) !== target.parentId)) return false;
  if (target.index === null) return true;
  const moving = new Set(sources);
  const remaining = remainingChildren(model, target.parentId, moving);
  const index = Math.min(Math.max(target.index, 0), remaining.length);
  const next = [...remaining.slice(0, index), ...sources, ...remaining.slice(index)];
  const current = childIds(model, target.parentId);
  return next.length === current.length && next.every((id, i) => id === current[i]);
}

/**
 * Resolve the pointer over a row (or blank space when `overId` is null) to a landing
 * spot, or null when the drop is not allowed or would change nothing.
 */
export function resolveDropTarget(
  model: TreeModel,
  sources: readonly string[],
  overId: string | null,
  zone: DropZone,
  policy: DropPolicy,
): DropTarget | null {
  if (!sources.length) return null;
  const moving = new Set(sources);
  let target: DropTarget;
  if (overId === null || !hasNode(model, overId)) {
    if (overId !== null) return null;
    const parentId = policy.blankParent ?? TREE_ROOT;
    target = {
      parentId,
      index: policy.ordered ? remainingChildren(model, parentId, moving).length : null,
      into: true,
    };
  } else {
    const branch = isBranch(model, overId);
    const effectiveZone: DropZone = policy.intoOnly?.(overId) ? "into" : zone;
    const parentOfOver = model.parent.get(overId) ?? TREE_ROOT;
    if (!policy.ordered) {
      target = branch && effectiveZone === "into"
        ? { parentId: overId, index: null, into: true }
        : { parentId: parentOfOver, index: null, into: true };
    } else if (effectiveZone === "into" && branch) {
      target = { parentId: overId, index: remainingChildren(model, overId, moving).length, into: true };
    } else if (effectiveZone === "after" && branch && (policy.isExpanded?.(overId) ?? false) && childIds(model, overId).length > 0) {
      // Below an open folder row is the slot above its first child.
      target = { parentId: overId, index: 0, into: false };
    } else {
      const fullIndex = childIds(model, parentOfOver).indexOf(overId) + (effectiveZone === "after" ? 1 : 0);
      target = { parentId: parentOfOver, index: remainingIndex(model, parentOfOver, fullIndex, moving), into: false };
    }
  }
  // Never into itself or anything below it.
  if (target.parentId !== TREE_ROOT && sources.some((id) => isSelfOrDescendant(model, id, target.parentId))) return null;
  if (target.parentId !== TREE_ROOT && !hasNode(model, target.parentId)) return null;
  if (target.parentId !== TREE_ROOT && !isBranch(model, target.parentId)) return null;
  if (policy.acceptsDrop && !policy.acceptsDrop(target.parentId, sources)) return null;
  if (target.index !== null && policy.leavesFirst) {
    const remaining = remainingChildren(model, target.parentId, moving);
    const movingBranch = isBranch(model, sources[0]);
    target = { ...target, index: clampForKind(model, remaining, target.index, movingBranch) };
  }
  if (isNoopDrop(model, sources, target)) return null;
  return target;
}

/** Move whole nodes (with everything inside them) to the target. Returns a new model. */
export function moveNodes(
  model: TreeModel,
  sources: readonly string[],
  target: DropTarget,
  policy: Pick<DropPolicy, "leavesFirst"> = {},
): TreeModel {
  const moving = topLevelSources(model, sources).filter(
    (id) => target.parentId === TREE_ROOT || !isSelfOrDescendant(model, id, target.parentId),
  );
  if (!moving.length) return model;
  const children = new Map<string, readonly string[]>(model.children);
  const parent = new Map(model.parent);
  const movingSet = new Set(moving);
  const touched = new Set<string>();
  for (const id of moving) {
    const from = model.parent.get(id) ?? TREE_ROOT;
    touched.add(from);
  }
  for (const from of touched) {
    children.set(from, (children.get(from) ?? []).filter((id) => !movingSet.has(id)));
  }
  const destination = [...(children.get(target.parentId) ?? [])];
  const index = target.index === null ? destination.length : Math.min(Math.max(target.index, 0), destination.length);
  destination.splice(index, 0, ...moving);
  const placed = policy.leavesFirst
    ? [...destination.filter((id) => !model.branches.has(id)), ...destination.filter((id) => model.branches.has(id))]
    : destination;
  children.set(target.parentId, placed);
  for (const id of moving) parent.set(id, target.parentId);
  return { children, parent, branches: model.branches };
}

/** How the tree shows where a drop lands. */
export type DropIndicator =
  | { kind: "line"; anchorId: string; edge: "before" | "after" }
  /** A highlighted branch row ("" is the tree itself). */
  | { kind: "into"; targetId: string };

export function indicatorFor(
  model: TreeModel,
  sources: readonly string[],
  target: DropTarget,
  policy: Pick<DropPolicy, "isExpanded"> = {},
): DropIndicator {
  if (target.index === null || target.into) return { kind: "into", targetId: target.parentId };
  if (target.parentId !== TREE_ROOT && policy.isExpanded && !policy.isExpanded(target.parentId)) {
    return { kind: "into", targetId: target.parentId };
  }
  const remaining = remainingChildren(model, target.parentId, new Set(sources));
  if (!remaining.length) return { kind: "into", targetId: target.parentId };
  if (target.index < remaining.length) return { kind: "line", anchorId: remaining[target.index], edge: "before" };
  return { kind: "line", anchorId: remaining[remaining.length - 1], edge: "after" };
}

/** Where each node sits, to undo a move later. Ordered so restoring in this order works. */
export function originalPlaces(model: TreeModel, sources: readonly string[]): Array<{ id: string; parentId: string; index: number }> {
  const places = sources
    .map((id) => {
      const at = locate(model, id);
      return at ? { id, parentId: at.parentId, index: at.index } : null;
    })
    .filter((place): place is { id: string; parentId: string; index: number } => place !== null);
  return places.sort((a, b) => a.index - b.index);
}

/** Put nodes back where `originalPlaces` saw them (the undo of a move). */
export function restorePlaces(
  model: TreeModel,
  places: ReadonlyArray<{ id: string; parentId: string; index: number }>,
): TreeModel {
  let next = model;
  for (const place of places) {
    if (!hasNode(next, place.id)) continue;
    if (place.parentId !== TREE_ROOT && !hasNode(next, place.parentId)) continue;
    if (place.parentId !== TREE_ROOT && isSelfOrDescendant(next, place.id, place.parentId)) continue;
    const remaining = childIds(next, place.parentId).filter((id) => id !== place.id);
    next = moveNodes(next, [place.id], {
      parentId: place.parentId,
      index: Math.min(place.index, remaining.length),
      into: false,
    });
  }
  return next;
}

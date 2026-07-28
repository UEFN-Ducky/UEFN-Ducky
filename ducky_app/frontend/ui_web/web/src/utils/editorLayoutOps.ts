import type { EditorDropZone, EditorGroup, EditorLayoutNode, EditorLayoutState, SplitAxis } from "../types/panel";

let nextId = 0;
function uid(prefix: string): string {
  nextId += 1;
  return `${prefix}-${nextId}-${Math.random().toString(36).slice(2, 7)}`;
}

export function createGroup(tabIds: string[] = [], activeTabId: string | null = null): EditorGroup {
  const id = uid("grp");
  const active = activeTabId && tabIds.includes(activeTabId) ? activeTabId : tabIds[tabIds.length - 1] ?? null;
  return { id, tabIds: [...tabIds], activeTabId: active };
}

export function createDefaultLayout(tabIds: string[]): EditorLayoutState {
  const group = createGroup(tabIds);
  return {
    root: { type: "group", groupId: group.id },
    groups: { [group.id]: group },
    focusedGroupId: group.id,
  };
}

export function createEmptyLayout(): EditorLayoutState {
  const group = createGroup();
  return {
    root: { type: "group", groupId: group.id },
    groups: { [group.id]: group },
    focusedGroupId: group.id,
  };
}

export function collectTabIds(layout: EditorLayoutState): string[] {
  const ids = new Set<string>();
  for (const group of Object.values(layout.groups)) {
    for (const tabId of group.tabIds) ids.add(tabId);
  }
  return [...ids];
}

export function findGroupForTab(layout: EditorLayoutState, tabId: string): string | null {
  for (const group of Object.values(layout.groups)) {
    if (group.tabIds.includes(tabId)) return group.id;
  }
  return null;
}

function cloneLayout(layout: EditorLayoutState): EditorLayoutState {
  return {
    root: structuredClone(layout.root),
    groups: Object.fromEntries(Object.entries(layout.groups).map(([k, g]) => [k, { ...g, tabIds: [...g.tabIds] }])),
    focusedGroupId: layout.focusedGroupId,
  };
}

function updateGroup(layout: EditorLayoutState, groupId: string, patch: Partial<EditorGroup>): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group) return layout;
  return {
    ...layout,
    groups: { ...layout.groups, [groupId]: { ...group, ...patch, tabIds: patch.tabIds ?? group.tabIds } },
  };
}

export function isCrossGroupTabMoveBlocked(
  layout: EditorLayoutState,
  sourceGroupId: string,
  targetGroupId: string,
): boolean {
  if (sourceGroupId === targetGroupId) return false;
  const source = layout.groups[sourceGroupId];
  const target = layout.groups[targetGroupId];
  return !!(source?.locked || target?.locked);
}

export function toggleGroupLock(layout: EditorLayoutState, groupId: string): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group) return layout;
  return updateGroup(layout, groupId, { locked: !group.locked });
}

function removeTabFromGroup(layout: EditorLayoutState, groupId: string, tabId: string): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group) return layout;
  const tabIds = group.tabIds.filter((id) => id !== tabId);
  let activeTabId = group.activeTabId;
  if (activeTabId === tabId) {
    const idx = group.tabIds.indexOf(tabId);
    activeTabId = tabIds[Math.min(idx, tabIds.length - 1)] ?? null;
  }
  return updateGroup(layout, groupId, { tabIds, activeTabId });
}

function replaceNode(
  node: EditorLayoutNode,
  groupId: string,
  replacement: EditorLayoutNode,
): EditorLayoutNode {
  if (node.type === "group") {
    return node.groupId === groupId ? replacement : node;
  }
  return {
    ...node,
    children: node.children.map((child) => replaceNode(child, groupId, replacement)),
  };
}

function collectGroupIdsInTree(node: EditorLayoutNode): Set<string> {
  if (node.type === "group") return new Set([node.groupId]);
  const ids = new Set<string>();
  for (const child of node.children) {
    for (const id of collectGroupIdsInTree(child)) ids.add(id);
  }
  return ids;
}

function collapseEmptyGroups(layout: EditorLayoutState): EditorLayoutState {
  const prune = (node: EditorLayoutNode): EditorLayoutNode | null => {
    if (node.type === "group") {
      const group = layout.groups[node.groupId];
      if (!group || group.tabIds.length === 0) return null;
      return node;
    }
    const children: EditorLayoutNode[] = [];
    const sizes: number[] = [];
    for (let i = 0; i < node.children.length; i += 1) {
      const pruned = prune(node.children[i]!);
      if (pruned) {
        children.push(pruned);
        sizes.push(node.sizes[i] ?? 1);
      }
    }
    if (children.length === 0) return null;
    if (children.length === 1) return children[0]!;
    const total = sizes.reduce((a, b) => a + b, 0) || children.length;
    return { ...node, children, sizes: sizes.map((s) => (s / total) * children.length) };
  };

  const root = prune(layout.root);
  if (!root) {
    const empty = createEmptyLayout();
    return { ...layout, root: empty.root, groups: empty.groups, focusedGroupId: empty.focusedGroupId };
  }

  const referencedIds = collectGroupIdsInTree(root);
  const groups: Record<string, EditorGroup> = {};
  for (const id of referencedIds) {
    const group = layout.groups[id];
    if (group) groups[id] = group;
  }

  let focusedGroupId = layout.focusedGroupId;
  if (!referencedIds.has(focusedGroupId)) {
    const withTabs = [...referencedIds].find((id) => (groups[id]?.tabIds.length ?? 0) > 0);
    focusedGroupId = withTabs ?? [...referencedIds][0] ?? focusedGroupId;
  }

  return { ...layout, root, groups, focusedGroupId };
}

export function resolveTargetGroupId(layout: EditorLayoutState): string {
  const focused = layout.groups[layout.focusedGroupId];
  if (focused?.tabIds.length) return layout.focusedGroupId;
  for (const group of Object.values(layout.groups)) {
    if (group.tabIds.length > 0) return group.id;
  }
  if (focused) return layout.focusedGroupId;
  return Object.keys(layout.groups)[0] ?? layout.focusedGroupId;
}

export function pruneLayoutTabIds(layout: EditorLayoutState, validTabIds: Iterable<string>): EditorLayoutState {
  const valid = new Set(validTabIds);
  const groups: EditorLayoutState["groups"] = {};
  for (const [id, group] of Object.entries(layout.groups)) {
    const tabIds = group.tabIds.filter((tid) => valid.has(tid));
    if (tabIds.length === 0) continue;
    const activeTabId =
      group.activeTabId && tabIds.includes(group.activeTabId)
        ? group.activeTabId
        : tabIds[tabIds.length - 1] ?? null;
    groups[id] = { ...group, tabIds, activeTabId };
  }
  let focusedGroupId = layout.focusedGroupId;
  if (!groups[focusedGroupId]) {
    focusedGroupId = resolveTargetGroupId({ ...layout, groups, focusedGroupId });
  }
  return collapseEmptyGroups({ ...layout, groups, focusedGroupId });
}

export function normalizeLayout(layout: EditorLayoutState): EditorLayoutState {
  return collapseEmptyGroups(layout);
}

export function removeTabFromLayout(layout: EditorLayoutState, tabId: string): EditorLayoutState {
  const groupId = findGroupForTab(layout, tabId);
  if (!groupId) return layout;
  let next = removeTabFromGroup(layout, groupId, tabId);
  next = collapseEmptyGroups(next);
  if (!next.groups[next.focusedGroupId]) {
    const first = Object.keys(next.groups)[0];
    if (first) next = { ...next, focusedGroupId: first };
  }
  return next;
}

export function activateTab(layout: EditorLayoutState, groupId: string, tabId: string): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group?.tabIds.includes(tabId)) return layout;
  return {
    ...updateGroup(layout, groupId, { activeTabId: tabId }),
    focusedGroupId: groupId,
  };
}

export function focusGroup(layout: EditorLayoutState, groupId: string): EditorLayoutState {
  if (!layout.groups[groupId]) return layout;
  return { ...layout, focusedGroupId: groupId };
}

export function reorderGroupTabs(
  layout: EditorLayoutState,
  groupId: string,
  draggedId: string,
  targetId: string,
  insertBefore: boolean,
): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group || draggedId === targetId) return layout;
  const fromIdx = group.tabIds.indexOf(draggedId);
  let toIdx = group.tabIds.indexOf(targetId);
  if (fromIdx < 0 || toIdx < 0) return layout;
  const tabIds = [...group.tabIds];
  const [moved] = tabIds.splice(fromIdx, 1);
  if (fromIdx < toIdx) toIdx -= 1;
  if (!insertBefore) toIdx += 1;
  tabIds.splice(toIdx, 0, moved!);
  return updateGroup(layout, groupId, { tabIds });
}

/** Reorder within one group, or move a tab onto another group's tab bar at a position. */
export function moveTabOnTabBar(
  layout: EditorLayoutState,
  sourceGroupId: string,
  targetGroupId: string,
  draggedId: string,
  targetId: string,
  insertBefore: boolean,
): EditorLayoutState {
  if (draggedId === targetId) return layout;
  if (sourceGroupId !== targetGroupId && isCrossGroupTabMoveBlocked(layout, sourceGroupId, targetGroupId)) {
    return layout;
  }
  if (sourceGroupId === targetGroupId) {
    return reorderGroupTabs(layout, targetGroupId, draggedId, targetId, insertBefore);
  }
  const targetGroup = layout.groups[targetGroupId];
  if (!targetGroup) return layout;
  let toIdx = targetGroup.tabIds.indexOf(targetId);
  if (toIdx < 0) {
    return moveTabToGroup(layout, draggedId, sourceGroupId, targetGroupId);
  }
  let next = removeTabFromGroup(layout, sourceGroupId, draggedId);
  const updatedTarget = next.groups[targetGroupId];
  if (!updatedTarget) return next;
  const tabIds = [...updatedTarget.tabIds];
  toIdx = tabIds.indexOf(targetId);
  if (toIdx < 0) {
    tabIds.push(draggedId);
  } else {
    if (!insertBefore) toIdx += 1;
    tabIds.splice(toIdx, 0, draggedId);
  }
  next = updateGroup(next, targetGroupId, { tabIds, activeTabId: draggedId });
  next = collapseEmptyGroups(next);
  return { ...next, focusedGroupId: targetGroupId };
}

export function moveTabToGroup(
  layout: EditorLayoutState,
  tabId: string,
  sourceGroupId: string,
  targetGroupId: string,
): EditorLayoutState {
  if (sourceGroupId === targetGroupId) return layout;
  if (isCrossGroupTabMoveBlocked(layout, sourceGroupId, targetGroupId)) return layout;
  const target = layout.groups[targetGroupId];
  if (!target || target.tabIds.includes(tabId)) return layout;
  let next = removeTabFromGroup(layout, sourceGroupId, tabId);
  const updatedTarget = next.groups[targetGroupId];
  if (!updatedTarget) return next;
  next = updateGroup(next, targetGroupId, {
    tabIds: [...updatedTarget.tabIds, tabId],
    activeTabId: tabId,
  });
  next = collapseEmptyGroups(next);
  return { ...next, focusedGroupId: targetGroupId };
}

function edgeToSplit(edge: EditorDropZone): { axis: SplitAxis; before: boolean } | null {
  if (edge === "left") return { axis: "row", before: true };
  if (edge === "right") return { axis: "row", before: false };
  if (edge === "top") return { axis: "column", before: true };
  if (edge === "bottom") return { axis: "column", before: false };
  return null;
}

export function splitGroupWithTab(
  layout: EditorLayoutState,
  targetGroupId: string,
  tabId: string,
  sourceGroupId: string,
  edge: EditorDropZone,
): EditorLayoutState {
  const split = edgeToSplit(edge);
  if (!split) return layout;
  if (isCrossGroupTabMoveBlocked(layout, sourceGroupId, targetGroupId)) return layout;

  let next = cloneLayout(layout);
  if (findGroupForTab(next, tabId)) {
    next = removeTabFromGroup(next, sourceGroupId, tabId);
  }

  const newGroup = createGroup([tabId], tabId);
  next = {
    ...next,
    groups: { ...next.groups, [newGroup.id]: newGroup },
    focusedGroupId: newGroup.id,
  };

  const existingGroupNode: EditorLayoutNode = { type: "group", groupId: targetGroupId };
  const newGroupNode: EditorLayoutNode = { type: "group", groupId: newGroup.id };
  const children = split.before ? [newGroupNode, existingGroupNode] : [existingGroupNode, newGroupNode];
  const splitNode: EditorLayoutNode = {
    type: "split",
    id: uid("split"),
    axis: split.axis,
    children,
    sizes: [1, 1],
  };

  next = { ...next, root: replaceNode(next.root, targetGroupId, splitNode) };
  next = collapseEmptyGroups(next);
  if (!next.groups[next.focusedGroupId]) {
    next = { ...next, focusedGroupId: newGroup.id };
  }
  return next;
}

export function insertTabIntoGroup(
  layout: EditorLayoutState,
  groupId: string,
  tabId: string,
  options?: { activate?: boolean },
): EditorLayoutState {
  const existingGroupId = findGroupForTab(layout, tabId);
  if (existingGroupId) {
    if (existingGroupId === groupId) {
      if (options?.activate === false) return normalizeLayout(layout);
      return normalizeLayout(activateTab(layout, groupId, tabId));
    }
    return normalizeLayout(moveTabToGroup(layout, tabId, existingGroupId, groupId));
  }
  const group = layout.groups[groupId];
  if (!group) return addTabToFocusedGroup(layout, tabId, options);
  return normalizeLayout(
    updateGroup(
      { ...layout, focusedGroupId: groupId },
      groupId,
      {
        tabIds: [...group.tabIds, tabId],
        activeTabId: options?.activate === false ? group.activeTabId : tabId,
      },
    ),
  );
}

export function handleGroupDrop(
  layout: EditorLayoutState,
  targetGroupId: string,
  tabId: string,
  sourceGroupId: string,
  zone: EditorDropZone,
): EditorLayoutState {
  // Cross-window drags carry the source group id from another webview — never split on those.
  if (!layout.groups[sourceGroupId]) {
    return insertTabIntoGroup(layout, targetGroupId, tabId, { activate: true });
  }
  if (zone === "center") {
    return moveTabToGroup(layout, tabId, sourceGroupId, targetGroupId);
  }
  return splitGroupWithTab(layout, targetGroupId, tabId, sourceGroupId, zone);
}

export function addTabToFocusedGroup(
  layout: EditorLayoutState,
  tabId: string,
  options?: { activate?: boolean },
): EditorLayoutState {
  let groupId = resolveTargetGroupId(layout);
  if (!layout.groups[groupId]) {
    const first = Object.keys(layout.groups)[0];
    if (!first) return createDefaultLayout([tabId]);
    groupId = first;
  }
  const group = layout.groups[groupId]!;
  if (group.tabIds.includes(tabId)) {
    if (options?.activate === false) return normalizeLayout(layout);
    return normalizeLayout(activateTab(layout, groupId, tabId));
  }
  return normalizeLayout(
    updateGroup(
      { ...layout, focusedGroupId: groupId },
      groupId,
      {
        tabIds: [...group.tabIds, tabId],
        activeTabId: options?.activate === false ? group.activeTabId : tabId,
      },
    ),
  );
}

export function insertTabIntoLayout(
  layout: EditorLayoutState,
  tabId: string,
  options?: { activate?: boolean },
): EditorLayoutState {
  if (findGroupForTab(layout, tabId)) {
    const groupId = findGroupForTab(layout, tabId)!;
    if (options?.activate === false) return normalizeLayout(layout);
    return normalizeLayout(activateTab(layout, groupId, tabId));
  }
  return addTabToFocusedGroup(layout, tabId, options);
}

/** Swap `oldId` for `newId` in place within `groupId` (used to reuse a preview tab's
 * slot for a newly single-clicked file). Falls back to a normal insert if the old tab
 * is no longer in that group. */
export function replaceTabIdInGroup(
  layout: EditorLayoutState,
  groupId: string,
  oldId: string,
  newId: string,
  activate = true,
): EditorLayoutState {
  const group = layout.groups[groupId];
  if (!group || !group.tabIds.includes(oldId)) {
    return insertTabIntoLayout(layout, newId, { activate });
  }
  const tabIds = group.tabIds.map((id) => (id === oldId ? newId : id));
  const activeTabId = activate ? newId : group.activeTabId === oldId ? newId : group.activeTabId;
  const next = updateGroup(
    { ...layout, focusedGroupId: activate ? groupId : layout.focusedGroupId },
    groupId,
    { tabIds, activeTabId },
  );
  return normalizeLayout(next);
}

export function remapTabIdInLayout(layout: EditorLayoutState, oldId: string, newId: string): EditorLayoutState {
  const groupId = findGroupForTab(layout, oldId);
  if (!groupId) return layout;
  const group = layout.groups[groupId]!;
  const tabIds = group.tabIds.map((id) => (id === oldId ? newId : id));
  const activeTabId = group.activeTabId === oldId ? newId : group.activeTabId;
  return updateGroup(layout, groupId, { tabIds, activeTabId });
}

export const MIN_SPLIT_PANE_SIZE = 30;

export function resizeSplitPair(
  layout: EditorLayoutState,
  splitId: string,
  childIndex: number,
  deltaRatio: number,
  minPaneRatio = MIN_SPLIT_PANE_SIZE / 1000,
): EditorLayoutState {
  const adjust = (node: EditorLayoutNode): EditorLayoutNode => {
    if (node.type === "group") return node;
    if (node.id !== splitId) {
      return { ...node, children: node.children.map(adjust) };
    }
    const sizes = [...node.sizes];
    const left = sizes[childIndex] ?? 1;
    const right = sizes[childIndex + 1] ?? 1;
    const pair = left + right;
    const min = Math.min(Math.max(minPaneRatio, 0), pair / 2 - 1e-6);
    let nextLeft = left + deltaRatio * pair;
    nextLeft = Math.max(min, Math.min(pair - min, nextLeft));
    sizes[childIndex] = nextLeft;
    sizes[childIndex + 1] = pair - nextLeft;
    return { ...node, sizes };
  };
  return { ...layout, root: adjust(layout.root) };
}

export function collectActiveTabIds(layout: EditorLayoutState): string[] {
  const ids: string[] = [];
  const walk = (node: EditorLayoutNode) => {
    if (node.type === "group") {
      const group = layout.groups[node.groupId];
      if (group?.activeTabId) ids.push(group.activeTabId);
      return;
    }
    for (const child of node.children) walk(child);
  };
  walk(layout.root);
  return ids;
}

function isLayoutRootValid(root: EditorLayoutNode, groups: Record<string, EditorGroup>): boolean {
  if (root.type === "group") return !!groups[root.groupId];
  return root.children.length > 0 && root.children.every((child) => isLayoutRootValid(child, groups));
}

/** Rebuild root when saved layout references pruned groups or splits collapsed to one tab. */
export function repairLayout(layout: EditorLayoutState): EditorLayoutState {
  const collapsed = collapseEmptyGroups(layout);
  const tabIds = collectTabIds(collapsed);
  if (tabIds.length === 0) return createEmptyLayout();
  if (tabIds.length === 1) return createDefaultLayout(tabIds);
  const groupsInTree = collectGroupIdsInTree(collapsed.root);
  const nonEmptyInTree = [...groupsInTree].filter((id) => (collapsed.groups[id]?.tabIds.length ?? 0) > 0);
  if (nonEmptyInTree.length <= 1 && groupsInTree.size > 1) {
    return createDefaultLayout(tabIds);
  }
  if (isLayoutRootValid(collapsed.root, collapsed.groups)) return collapsed;
  return createDefaultLayout(tabIds);
}

export function dropZoneFromPointer(rect: DOMRect, clientX: number, clientY: number, edgeRatio = 0.25): EditorDropZone {
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  const edgeW = rect.width * edgeRatio;
  const edgeH = rect.height * edgeRatio;
  const inLeft = x < edgeW;
  const inRight = x > rect.width - edgeW;
  const inTop = y < edgeH;
  const inBottom = y > rect.height - edgeH;
  if (inTop && inLeft) return x / rect.width < y / rect.height ? "top" : "left";
  if (inTop && inRight) return (rect.width - x) / rect.width < y / rect.height ? "top" : "right";
  if (inBottom && inLeft) return x / rect.width < (rect.height - y) / rect.height ? "bottom" : "left";
  if (inBottom && inRight) return (rect.width - x) / rect.width < (rect.height - y) / rect.height ? "bottom" : "right";
  if (inTop) return "top";
  if (inBottom) return "bottom";
  if (inLeft) return "left";
  if (inRight) return "right";
  return "center";
}

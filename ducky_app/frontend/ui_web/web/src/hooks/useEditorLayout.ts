import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { EditorLayoutState, EditorTab, EditorDropZone } from "../types/panel";
import {
  collectTabIds,
  createDefaultLayout,
  createEmptyLayout,
  findGroupForTab,
  handleGroupDrop,
  insertTabIntoGroup,
  insertTabIntoLayout,
  pruneLayoutTabIds,
  remapTabIdInLayout,
  removeTabFromLayout,
  repairLayout,
  replaceTabIdInGroup,
  resolveTargetGroupId,
  moveTabOnTabBar,
  moveTabToGroup,
  activateTab,
  focusGroup,
  normalizeLayout,
  splitGroupWithTab,
  toggleGroupLock,
} from "../utils/editorLayoutOps";
import { emitAppHook } from "../sfx/appHooks";

function addOpenTab(prev: EditorTab[], tab: EditorTab): EditorTab[] {
  return prev.some((t) => t.id === tab.id) ? prev : [...prev, tab];
}

export function useEditorLayout(initialOpenTabs: EditorTab[] = []) {
  const [openTabs, setOpenTabs] = useState<EditorTab[]>(initialOpenTabs);
  const [layout, setLayout] = useState<EditorLayoutState>(() =>
    initialOpenTabs.length > 0
      ? createDefaultLayout(initialOpenTabs.map((t) => t.id))
      : createEmptyLayout(),
  );
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  const setLayoutState = useCallback((updater: SetStateAction<EditorLayoutState>) => {
    setLayout((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      return repairLayout(normalizeLayout(next));
    });
  }, []);

  const syncLayoutWithTabs = useCallback((layout: EditorLayoutState, tabs: EditorTab[]) => {
    return repairLayout(pruneLayoutTabIds(layout, tabs.map((t) => t.id)));
  }, []);

  const openTab = useCallback(
    (tab: EditorTab, options?: { activate?: boolean; preview?: boolean }) => {
      const preview = options?.preview ?? false;
      setOpenTabs((prev) => {
        const existing = prev.find((t) => t.id === tab.id);
        if (existing) {
          // Already open: a permanent open pins a preview tab; otherwise leave state as-is.
          const nextTabs =
            !preview && existing.preview
              ? prev.map((t) => (t.id === tab.id ? { ...t, preview: false } : t))
              : prev;
          setLayout((prevLayout) =>
            syncLayoutWithTabs(insertTabIntoLayout(prevLayout, tab.id, options), nextTabs),
          );
          return nextTabs;
        }

        const newTab: EditorTab = preview ? { ...tab, preview: true } : tab;

        // Preview open: reuse the existing preview tab's slot in the target group so
        // single-clicking through files never stacks up tabs (VS Code preview rule).
        if (preview) {
          const lay = layoutRef.current;
          const targetGroupId = resolveTargetGroupId(lay);
          const group = lay.groups[targetGroupId];
          const previewInGroup = group
            ? prev.find((t) => t.preview && group.tabIds.includes(t.id))
            : undefined;
          if (previewInGroup) {
            const nextTabs = prev.map((t) => (t.id === previewInGroup.id ? newTab : t));
            const activate = options?.activate !== false;
            setLayout((prevLayout) =>
              syncLayoutWithTabs(
                replaceTabIdInGroup(prevLayout, targetGroupId, previewInGroup.id, newTab.id, activate),
                nextTabs,
              ),
            );
            return nextTabs;
          }
        }

        const nextTabs = [...prev, newTab];
        setLayout((prevLayout) =>
          syncLayoutWithTabs(insertTabIntoLayout(prevLayout, newTab.id, options), nextTabs),
        );
        return nextTabs;
      });
    },
    [syncLayoutWithTabs],
  );

  /** Pin a preview tab (clear its italic preview state) so it stops being reused. */
  const promoteTab = useCallback((tabId: string) => {
    setOpenTabs((prev) => {
      const t = prev.find((x) => x.id === tabId);
      if (!t || !t.preview) return prev;
      return prev.map((x) => (x.id === tabId ? { ...x, preview: false } : x));
    });
  }, []);

  const closeTabInLayout = useCallback((tabId: string) => {
    setOpenTabs((prev) => {
      const nextTabs = prev.filter((t) => t.id !== tabId);
      setLayout((prevLayout) =>
        syncLayoutWithTabs(removeTabFromLayout(prevLayout, tabId), nextTabs),
      );
      return nextTabs;
    });
  }, [syncLayoutWithTabs]);

  const activateTabInGroup = useCallback((groupId: string, tabId: string) => {
    setLayout((prev) => activateTab(prev, groupId, tabId));
    emitAppHook("tab.changed", { groupId, tabId });
  }, []);

  const setFocusedGroup = useCallback((groupId: string) => {
    setLayout((prev) => focusGroup(prev, groupId));
  }, []);

  const reorderTabsInGroup = useCallback(
    (
      groupId: string,
      draggedId: string,
      targetId: string,
      insertBefore: boolean,
      sourceGroupId?: string,
    ) => {
      setLayout((prev) =>
        moveTabOnTabBar(prev, sourceGroupId ?? groupId, groupId, draggedId, targetId, insertBefore),
      );
    },
    [],
  );

  const dropTabOnGroup = useCallback(
    (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => {
      setLayout((prev) =>
        syncLayoutWithTabs(
          handleGroupDrop(prev, targetGroupId, tabId, sourceGroupId, zone),
          openTabsRef.current,
        ),
      );
    },
    [syncLayoutWithTabs],
  );

  const restoreTabToLayout = useCallback((tabId: string) => {
    setLayout((prev) => repairLayout(insertTabIntoLayout(prev, tabId, { activate: true })));
  }, []);

  const remapTabId = useCallback((oldId: string, newId: string) => {
    setOpenTabs((prev) =>
      prev.map((t) => (t.id === oldId ? { ...t, id: newId } : t)),
    );
    setLayout((prev) => remapTabIdInLayout(prev, oldId, newId));
  }, []);

  const initLayoutFromTabs = useCallback((tabs: EditorTab[]) => {
    setOpenTabs(tabs);
    setLayout(tabs.length > 0 ? createDefaultLayout(tabs.map((t) => t.id)) : createEmptyLayout());
  }, []);

  const initLayoutState = useCallback((tabs: EditorTab[], nextLayout: EditorLayoutState) => {
    setOpenTabs(tabs);
    setLayout(repairLayout(pruneLayoutTabIds(nextLayout, tabs.map((t) => t.id))));
  }, []);

  const splitFocusedGroupWithTab = useCallback((otherTabId: string) => {
    setLayout((prev) => {
      const focusedId = prev.focusedGroupId;
      const sourceGroupId = findGroupForTab(prev, otherTabId) ?? focusedId;
      return handleGroupDrop(prev, focusedId, otherTabId, sourceGroupId, "right");
    });
  }, []);

  const toggleGroupLockInLayout = useCallback((groupId: string) => {
    setLayout((prev) => toggleGroupLock(prev, groupId));
  }, []);

  /** Open a tab in a group BESIDE the anchor tab's group (agent follow-code opens):
   * already-open tabs surface in place, otherwise reuse an existing side group
   * (preferring one that holds files), else split a new group right of the anchor.
   * Never steals keyboard focus from the anchor group. */
  const openTabBeside = useCallback(
    (tab: EditorTab, anchorTabId?: string | null) => {
      setOpenTabs((prev) => {
        const nextTabs = addOpenTab(prev, tab);
        setLayout((prevLayout) => {
          const keepFocus = prevLayout.focusedGroupId;
          const existingGroup = findGroupForTab(prevLayout, tab.id);
          let next;
          if (existingGroup) {
            next = activateTab(prevLayout, existingGroup, tab.id);
          } else {
            const anchorGroupId = anchorTabId ? findGroupForTab(prevLayout, anchorTabId) : null;
            if (!anchorGroupId) {
              next = insertTabIntoLayout(prevLayout, tab.id, { activate: false });
            } else {
              const kindOf = (tabId: string) => openTabsRef.current.find((t) => t.id === tabId)?.kind;
              const others = Object.values(prevLayout.groups).filter(
                (g) => g.id !== anchorGroupId && g.tabIds.length > 0,
              );
              const sideGroup =
                others.find((g) => g.tabIds.some((tid) => kindOf(tid) === "file")) ?? others[0];
              next = sideGroup
                ? insertTabIntoGroup(prevLayout, sideGroup.id, tab.id, { activate: true })
                : splitGroupWithTab(prevLayout, anchorGroupId, tab.id, anchorGroupId, "right");
            }
          }
          const focusedGroupId = next.groups[keepFocus] ? keepFocus : next.focusedGroupId;
          return syncLayoutWithTabs({ ...next, focusedGroupId }, nextTabs);
        });
        return nextTabs;
      });
    },
    [syncLayoutWithTabs],
  );

  /** Open (or move) a tab into a group at a VS Code-style drop zone — used when
   * dragging a ducky/file from a dock rail onto the editor. */
  const openTabInZone = useCallback(
    (tab: EditorTab, targetGroupId: string, zone: EditorDropZone) => {
      setOpenTabs((prev) => {
        const existing = prev.find((t) => t.id === tab.id);
        const nextTabs = existing
          ? !existing.preview && tab.preview
            ? prev
            : prev.map((t) => (t.id === tab.id ? { ...t, ...tab, preview: false } : t))
          : [...prev, { ...tab, preview: false }];
        setLayout((prevLayout) => {
          const groupId = prevLayout.groups[targetGroupId]
            ? targetGroupId
            : resolveTargetGroupId(prevLayout);
          const sourceGroupId = findGroupForTab(prevLayout, tab.id) ?? groupId;
          let next: EditorLayoutState;
          if (zone === "center") {
            next = findGroupForTab(prevLayout, tab.id)
              ? sourceGroupId === groupId
                ? activateTab(prevLayout, groupId, tab.id)
                : moveTabToGroup(prevLayout, tab.id, sourceGroupId, groupId)
              : insertTabIntoGroup(prevLayout, groupId, tab.id, { activate: true });
          } else {
            next = splitGroupWithTab(prevLayout, groupId, tab.id, sourceGroupId, zone);
          }
          return syncLayoutWithTabs(next, nextTabs);
        });
        return nextTabs;
      });
    },
    [syncLayoutWithTabs],
  );

  return {
    openTabs,
    setOpenTabs: setOpenTabs as Dispatch<SetStateAction<EditorTab[]>>,
    layout,
    setLayout: setLayoutState,
    openTabsRef,
    openTab,
    promoteTab,
    closeTabInLayout,
    activateTabInGroup,
    setFocusedGroup,
    reorderTabsInGroup,
    dropTabOnGroup,
    restoreTabToLayout,
    remapTabId,
    initLayoutFromTabs,
    initLayoutState,
    splitFocusedGroupWithTab,
    openTabBeside,
    openTabInZone,
    toggleGroupLockInLayout,
    collectLayoutTabIds: useCallback(() => collectTabIds(layout), [layout]),
  };
}

export type EditorLayoutApi = ReturnType<typeof useEditorLayout>;

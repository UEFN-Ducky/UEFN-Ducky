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

/** Tabs and layout change together: one state so an update batch (rapid clicks,
 * close-several-at-once) always sees the tabs AND groups the previous update produced.
 * Two separate useStates made preview opens read a stale layout and stack up tabs. */
interface EditorState {
  openTabs: EditorTab[];
  layout: EditorLayoutState;
}

function addOpenTab(prev: EditorTab[], tab: EditorTab): EditorTab[] {
  return prev.some((t) => t.id === tab.id) ? prev : [...prev, tab];
}

function syncLayoutWithTabs(layout: EditorLayoutState, tabs: EditorTab[]): EditorLayoutState {
  return repairLayout(pruneLayoutTabIds(layout, tabs.map((t) => t.id)));
}

function withLayout(state: EditorState, layout: EditorLayoutState): EditorState {
  return layout === state.layout ? state : { ...state, layout };
}

export function useEditorLayout(initialOpenTabs: EditorTab[] = []) {
  const [state, setState] = useState<EditorState>(() => ({
    openTabs: initialOpenTabs,
    layout:
      initialOpenTabs.length > 0
        ? createDefaultLayout(initialOpenTabs.map((t) => t.id))
        : createEmptyLayout(),
  }));
  const { openTabs, layout } = state;
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;

  const setOpenTabs = useCallback((updater: SetStateAction<EditorTab[]>) => {
    setState((s) => {
      const next = typeof updater === "function" ? updater(s.openTabs) : updater;
      return next === s.openTabs ? s : { ...s, openTabs: next };
    });
  }, []);

  const setLayoutState = useCallback((updater: SetStateAction<EditorLayoutState>) => {
    setState((s) => {
      const next = typeof updater === "function" ? updater(s.layout) : updater;
      return { ...s, layout: repairLayout(normalizeLayout(next)) };
    });
  }, []);

  const openTab = useCallback(
    (tab: EditorTab, options?: { activate?: boolean; preview?: boolean }) => {
      const preview = options?.preview ?? false;
      setState(({ openTabs: prev, layout: prevLayout }) => {
        const existing = prev.find((t) => t.id === tab.id);
        if (existing) {
          // Already open: a permanent open pins a preview tab; otherwise leave state as-is.
          const nextTabs =
            !preview && existing.preview
              ? prev.map((t) => (t.id === tab.id ? { ...t, preview: false } : t))
              : prev;
          return {
            openTabs: nextTabs,
            layout: syncLayoutWithTabs(insertTabIntoLayout(prevLayout, tab.id, options), nextTabs),
          };
        }

        const newTab: EditorTab = preview ? { ...tab, preview: true } : tab;

        // Preview open: reuse the existing preview tab's slot in the target group so
        // single-clicking through files never stacks up tabs (VS Code preview rule).
        if (preview) {
          const targetGroupId = resolveTargetGroupId(prevLayout);
          const group = prevLayout.groups[targetGroupId];
          const previewInGroup = group
            ? prev.find((t) => t.preview && group.tabIds.includes(t.id))
            : undefined;
          if (previewInGroup) {
            const nextTabs = prev.map((t) => (t.id === previewInGroup.id ? newTab : t));
            const activate = options?.activate !== false;
            return {
              openTabs: nextTabs,
              layout: syncLayoutWithTabs(
                replaceTabIdInGroup(prevLayout, targetGroupId, previewInGroup.id, newTab.id, activate),
                nextTabs,
              ),
            };
          }
        }

        const nextTabs = [...prev, newTab];
        return {
          openTabs: nextTabs,
          layout: syncLayoutWithTabs(insertTabIntoLayout(prevLayout, newTab.id, options), nextTabs),
        };
      });
    },
    [],
  );

  /** Pin a preview tab (clear its italic preview state) so it stops being reused. */
  const promoteTab = useCallback((tabId: string) => {
    setState((s) => {
      const t = s.openTabs.find((x) => x.id === tabId);
      if (!t || !t.preview) return s;
      return {
        ...s,
        openTabs: s.openTabs.map((x) => (x.id === tabId ? { ...x, preview: false } : x)),
      };
    });
  }, []);

  const closeTabInLayout = useCallback((tabId: string) => {
    setState(({ openTabs: prev, layout: prevLayout }) => {
      const nextTabs = prev.filter((t) => t.id !== tabId);
      return {
        openTabs: nextTabs,
        layout: syncLayoutWithTabs(removeTabFromLayout(prevLayout, tabId), nextTabs),
      };
    });
  }, []);

  const activateTabInGroup = useCallback((groupId: string, tabId: string) => {
    setState((s) => withLayout(s, activateTab(s.layout, groupId, tabId)));
    emitAppHook("tab.changed", { groupId, tabId });
  }, []);

  const setFocusedGroup = useCallback((groupId: string) => {
    setState((s) => withLayout(s, focusGroup(s.layout, groupId)));
  }, []);

  const reorderTabsInGroup = useCallback(
    (
      groupId: string,
      draggedId: string,
      targetId: string,
      insertBefore: boolean,
      sourceGroupId?: string,
    ) => {
      setState((s) =>
        withLayout(
          s,
          moveTabOnTabBar(s.layout, sourceGroupId ?? groupId, groupId, draggedId, targetId, insertBefore),
        ),
      );
    },
    [],
  );

  const dropTabOnGroup = useCallback(
    (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => {
      setState((s) =>
        withLayout(
          s,
          syncLayoutWithTabs(
            handleGroupDrop(s.layout, targetGroupId, tabId, sourceGroupId, zone),
            s.openTabs,
          ),
        ),
      );
    },
    [],
  );

  const restoreTabToLayout = useCallback((tabId: string) => {
    setState((s) =>
      withLayout(s, repairLayout(insertTabIntoLayout(s.layout, tabId, { activate: true }))),
    );
  }, []);

  const remapTabId = useCallback((oldId: string, newId: string) => {
    setState((s) => ({
      openTabs: s.openTabs.map((t) => (t.id === oldId ? { ...t, id: newId } : t)),
      layout: remapTabIdInLayout(s.layout, oldId, newId),
    }));
  }, []);

  const initLayoutFromTabs = useCallback((tabs: EditorTab[]) => {
    setState({
      openTabs: tabs,
      layout: tabs.length > 0 ? createDefaultLayout(tabs.map((t) => t.id)) : createEmptyLayout(),
    });
  }, []);

  const initLayoutState = useCallback((tabs: EditorTab[], nextLayout: EditorLayoutState) => {
    setState({
      openTabs: tabs,
      layout: repairLayout(pruneLayoutTabIds(nextLayout, tabs.map((t) => t.id))),
    });
  }, []);

  const splitFocusedGroupWithTab = useCallback((otherTabId: string) => {
    setState((s) => {
      const focusedId = s.layout.focusedGroupId;
      const sourceGroupId = findGroupForTab(s.layout, otherTabId) ?? focusedId;
      return withLayout(s, handleGroupDrop(s.layout, focusedId, otherTabId, sourceGroupId, "right"));
    });
  }, []);

  const toggleGroupLockInLayout = useCallback((groupId: string) => {
    setState((s) => withLayout(s, toggleGroupLock(s.layout, groupId)));
  }, []);

  /** Open a tab in a group BESIDE the anchor tab's group (agent follow-code opens):
   * already-open tabs surface in place, otherwise reuse an existing side group
   * (preferring one that holds files), else split a new group right of the anchor.
   * Never steals keyboard focus from the anchor group. */
  const openTabBeside = useCallback((tab: EditorTab, anchorTabId?: string | null) => {
    setState(({ openTabs: prev, layout: prevLayout }) => {
      const nextTabs = addOpenTab(prev, tab);
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
          const kindOf = (tabId: string) => nextTabs.find((t) => t.id === tabId)?.kind;
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
      return {
        openTabs: nextTabs,
        layout: syncLayoutWithTabs({ ...next, focusedGroupId }, nextTabs),
      };
    });
  }, []);

  /** Open (or move) a tab into a group at a VS Code-style drop zone — used when
   * dragging a ducky/file from a dock rail onto the editor. */
  const openTabInZone = useCallback(
    (tab: EditorTab, targetGroupId: string, zone: EditorDropZone) => {
      setState(({ openTabs: prev, layout: prevLayout }) => {
        const existing = prev.find((t) => t.id === tab.id);
        const nextTabs = existing
          ? !existing.preview && tab.preview
            ? prev
            : prev.map((t) => (t.id === tab.id ? { ...t, ...tab, preview: false } : t))
          : [...prev, { ...tab, preview: false }];
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
        return { openTabs: nextTabs, layout: syncLayoutWithTabs(next, nextTabs) };
      });
    },
    [],
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

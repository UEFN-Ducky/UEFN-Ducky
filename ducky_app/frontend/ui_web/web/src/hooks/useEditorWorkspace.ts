import { useCallback, useEffect, useRef } from "react";
import type { ChatTab, EditorTab, EditorLayoutState, EditorWorkspaceSnapshot, FocusWindowSnapshot } from "../types/panel";
import { automationsTabId, changesTabId, pipelinesTabId } from "../types/panel";
import { getApi } from "./usePanelApi";
import { collectTabIds, createDefaultLayout, repairLayout } from "../utils/editorLayoutOps";

const DEBOUNCE_MS = 300;

/** Survives ChatView remounts — a per-hook ref was resetting and re-running restore
 * (close_all_focus_windows) every time the tree remounted. */
let _restoredWorkspaceSlug: string | null = null;

function buildSnapshot(openTabs: EditorTab[], layout: EditorLayoutState): EditorWorkspaceSnapshot {
  return {
    version: 1,
    openTabs: openTabs.filter((t) => t.kind !== "terminal"),
    layout,
  };
}

async function fileExists(path: string): Promise<boolean> {
  const api = getApi();
  if (!api) return false;
  try {
    // A stat is enough — reading every restored file's content just to test for
    // existence made a workspace with many tabs slow to come back.
    if (typeof api.stat_project_file === "function") return (await api.stat_project_file(path)).exists;
    await api.read_project_file(path);
    return true;
  } catch {
    return false;
  }
}

async function existingSnapshotFiles(snapshot: EditorWorkspaceSnapshot, cancelled: () => boolean): Promise<Set<string>> {
  const paths = [...new Set([
    ...(snapshot.openTabs ?? []).flatMap((tab) => tab.kind === "file" && tab.path ? [tab.path] : []),
    ...(snapshot.focusWindows ?? []).flatMap((fw) => fw.tabIds.filter((id) => id.startsWith("file:")).map((id) => id.slice(5))),
  ].map((path) => path.replace(/\\/g, "/")))];
  const existing = new Set<string>();
  let index = 0;
  const worker = async () => {
    while (!cancelled() && index < paths.length) {
      const path = paths[index++]!;
      if (await fileExists(path)) existing.add(path);
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, paths.length) }, worker));
  return existing;
}

function validateSnapshot(
  snapshot: EditorWorkspaceSnapshot,
  allChats: ChatTab[],
  existingFiles: Set<string>,
): { openTabs: EditorTab[]; layout: EditorLayoutState } {
  const chatById = new Map(allChats.map((c) => [c.id, c]));
  const openTabs: EditorTab[] = [];

  for (const tab of snapshot.openTabs ?? []) {
    if (tab.kind === "chat") {
      if (!tab.chatId || !chatById.has(tab.chatId)) continue;
      const chat = chatById.get(tab.chatId)!;
      openTabs.push({
        ...tab,
        name: chat.name,
        duckyStyle: chat.duckyStyle,
      });
    } else if (tab.kind === "file" && tab.path) {
      const norm = tab.path.replace(/\\/g, "/");
      if (existingFiles.has(norm)) {
        openTabs.push({ ...tab, path: norm });
      }
    } else if (tab.kind === "settings") {
      openTabs.push({
        id: "settings:main",
        kind: "settings",
        name: tab.name || "Settings",
      });
    } else if (tab.kind === "changes") {
      openTabs.push({
        id: changesTabId(),
        kind: "changes",
        name: tab.name || "Ledger",
      });
    } else if (tab.kind === "automations") {
      openTabs.push({
        id: automationsTabId(),
        kind: "automations",
        name: tab.name || "Automations",
      });
    } else if (tab.kind === "pipelines") {
      openTabs.push({
        id: pipelinesTabId(),
        kind: "pipelines",
        name: tab.name || "Pipelines",
      });
    } else if (tab.kind === "ducky-profile" && tab.path) {
      openTabs.push({
        id: `ducky-profile:${tab.path}`,
        kind: "ducky-profile",
        name: tab.name || tab.path,
        path: tab.path,
        duckyStyle: tab.duckyStyle,
      });
    } else if (tab.kind === "terminal") {
      continue;
    }
  }

  const tabIds = new Set(openTabs.map((t) => t.id));

  let layout = snapshot.layout;
  if (!layout?.groups || !layout.root) {
    layout = openTabs.length > 0 ? createDefaultLayout(openTabs.map((t) => t.id)) : createDefaultLayout([]);
  } else {
    const groups: EditorLayoutState["groups"] = {};
    for (const [gid, group] of Object.entries(layout.groups)) {
      const filteredTabIds = group.tabIds.filter((id) => tabIds.has(id));
      if (filteredTabIds.length === 0) continue;
      const activeTabId =
        group.activeTabId && filteredTabIds.includes(group.activeTabId)
          ? group.activeTabId
          : filteredTabIds[filteredTabIds.length - 1] ?? null;
      groups[gid] = { ...group, tabIds: filteredTabIds, activeTabId };
    }
    const layoutTabIds = new Set(collectTabIds({ ...layout, groups }));
    for (const tab of openTabs) {
      if (!layoutTabIds.has(tab.id)) {
        const focusId = layout.focusedGroupId;
        if (groups[focusId]) {
          groups[focusId] = {
            ...groups[focusId],
            tabIds: [...groups[focusId].tabIds, tab.id],
            activeTabId: groups[focusId].activeTabId ?? tab.id,
          };
        } else {
          const fresh = createDefaultLayout(openTabs.map((t) => t.id));
          layout = fresh;
          groups[Object.keys(fresh.groups)[0]!] = fresh.groups[Object.keys(fresh.groups)[0]!]!;
        }
      }
    }
    if (Object.keys(groups).length === 0) {
      layout = openTabs.length > 0 ? createDefaultLayout(openTabs.map((t) => t.id)) : createDefaultLayout([]);
    } else {
      layout = repairLayout({
        ...layout,
        groups,
        focusedGroupId: groups[layout.focusedGroupId] ? layout.focusedGroupId : Object.keys(groups)[0]!,
      });
    }
  }

  return { openTabs, layout };
}

interface UseEditorWorkspaceOptions {
  projectSlug: string;
  allChats: ChatTab[];
  foldersLoaded: boolean;
  openTabs: EditorTab[];
  layout: EditorLayoutState;
  initLayoutState: (tabs: EditorTab[], layout: EditorLayoutState) => void;
}

export function useEditorWorkspace({
  projectSlug,
  allChats,
  foldersLoaded,
  openTabs,
  layout,
  initLayoutState,
}: UseEditorWorkspaceOptions) {
  const isRestoringRef = useRef(false);
  const restoreGenerationRef = useRef(0);
  const stateRef = useRef({ openTabs, layout });
  const allChatsRef = useRef(allChats);
  const projectSlugRef = useRef(projectSlug);
  stateRef.current = { openTabs, layout };
  allChatsRef.current = allChats;
  projectSlugRef.current = projectSlug;

  const saveNow = useCallback(async () => {
    const api = getApi();
    // Never persist before this project's snapshot has been applied (or deliberately
    // skipped): the empty pre-restore state — or the previous project's tabs right
    // after a switch — would overwrite the saved workspace.
    if (!api || isRestoringRef.current || _restoredWorkspaceSlug !== projectSlugRef.current) return;
    const { openTabs: tabs, layout: lay } = stateRef.current;
    try {
      await api.save_editor_workspace(buildSnapshot(tabs, lay));
    } catch {
      // Ignore transient AppData write races on Windows.
    }
  }, []);

  const flushBeforeSwitch = useCallback(async () => {
    await saveNow();
  }, [saveNow]);

  useEffect(() => {
    if (isRestoringRef.current) return;
    const timer = window.setTimeout(() => void saveNow(), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [openTabs, layout, saveNow]);

  useEffect(() => {
    return () => {
      void saveNow();
    };
  }, [saveNow]);

  useEffect(() => {
    if (!foldersLoaded) return;
    // foldersLoaded flaps false→true on every sidebar refresh; only restore once per project.
    if (_restoredWorkspaceSlug === projectSlug) return;

    const previousSlug = _restoredWorkspaceSlug;
    const switchingProject = previousSlug != null && previousSlug !== projectSlug;
    _restoredWorkspaceSlug = projectSlug;

    let cancelled = false;
    let settled = false;
    const generation = ++restoreGenerationRef.current;

    const restore = async () => {
      isRestoringRef.current = true;
      const api = getApi();
      if (!api) {
        // Allow a later retry when the API appears.
        _restoredWorkspaceSlug = previousSlug;
        isRestoringRef.current = false;
        return;
      }

      try {
        // close_all only when leaving another project — never on first load or
        // remount (that was killing focus windows the user just opened).
        if (switchingProject) {
          await api.close_all_focus_windows();
        }
        if (cancelled) return;
        const raw = await api.get_editor_workspace(projectSlug);
        if (cancelled) return;
        const focusWindows = (raw as EditorWorkspaceSnapshot).focusWindows ?? [];
        // Snapshot hygiene: validateSnapshot drops file tabs whose path no longer
        // exists (renamed/deleted files never come back as ghost tabs). Main tabs
        // only — focus windows reopen after layout init.
        const existingFiles = await existingSnapshotFiles(raw as EditorWorkspaceSnapshot, () => cancelled);
        const validated = validateSnapshot(raw as EditorWorkspaceSnapshot, allChatsRef.current, existingFiles);

        if (cancelled) return;

        // User may have opened/moved tabs (incl. into focus) while we awaited disk.
        // Stomping main openTabs re-claims those ids and destroys the focus window.
        const applyFromDisk =
          switchingProject || stateRef.current.openTabs.length === 0;
        settled = true;
        if (!applyFromDisk) return;

        initLayoutState(validated.openTabs, validated.layout);

        if (focusWindows.length > 0) {
          const chatIds = new Set(allChatsRef.current.map((c) => c.id));
          const restoredFocus: FocusWindowSnapshot[] = [];
          for (const fw of focusWindows) {
            const tabIds: string[] = [];
            for (const id of fw.tabIds) {
              if (id.startsWith("chat:")) {
                if (chatIds.has(id.slice(5))) tabIds.push(id);
              } else if (id.startsWith("file:")) {
                if (existingFiles.has(id.slice(5).replace(/\\/g, "/"))) tabIds.push(id);
              } else {
                tabIds.push(id);
              }
            }
            if (tabIds.length === 0) continue;
            restoredFocus.push({
              ...fw,
              tabIds,
              birthTabId: tabIds.includes(fw.birthTabId) ? fw.birthTabId : tabIds[0]!,
            });
          }
          if (restoredFocus.length > 0 && !cancelled) {
            await api.restore_focus_windows(restoredFocus);
          }
        }
      } catch (error) {
        if (!cancelled) {
          console.error("[workspace] restore failed", error);
          if (_restoredWorkspaceSlug === projectSlug) _restoredWorkspaceSlug = previousSlug;
        }
      } finally {
        if (!cancelled) {
          window.setTimeout(() => {
            if (restoreGenerationRef.current === generation) isRestoringRef.current = false;
          }, 0);
        }
      }
    };

    void restore();
    return () => {
      cancelled = true;
      restoreGenerationRef.current++;
      isRestoringRef.current = false;
      // Cancelled before the snapshot was applied (remount, foldersLoaded flap): let
      // the next run retry instead of treating this project as restored.
      if (!settled && _restoredWorkspaceSlug === projectSlug) _restoredWorkspaceSlug = previousSlug;
    };
  }, [projectSlug, foldersLoaded, initLayoutState]);

  return { flushBeforeSwitch };
}

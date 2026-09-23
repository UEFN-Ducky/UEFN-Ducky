import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import type { ProjectFileEntry, WorkspaceRootEntry } from "../types/panel";
import { isProjectContentRoot } from "../utils/contentTreeProjects";
import { isBrowsableTreeDir } from "../utils/fileTreeDrag";
import { WORKSPACE_ROOTS_PATH } from "../verse-editor/utils/isVerseFile";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";

type DirCache = Map<string, ProjectFileEntry[]>;

/** Content is usable as soon as its own listing arrives. Core/expanded folders
 * stream in afterwards; old projects cannot publish into the new tree. */
export function useWorkspaceTreeData(
  projectSlug: string,
  refreshToken: number,
  isActive: boolean,
  expandedPathsRef: MutableRefObject<Set<string>>,
) {
  const [rootEntries, setRootEntries] = useState<ProjectFileEntry[]>([]);
  const [workspaceRoots, setWorkspaceRoots] = useState<WorkspaceRootEntry[]>([]);
  const [cache, setCache] = useState<DirCache>(() => new Map());
  const [error, setError] = useState<string | null>(null);
  const [loadingRoot, setLoadingRoot] = useState(true);
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(() => new Set());
  const session = useMemo(() => ({
    active: true,
    directories: new Map<string, Promise<ProjectFileEntry[]>>(),
    refresh: null as Promise<void> | null,
    refreshAgain: false,
  }), [projectSlug]);
  const sessionRef = useRef(session);
  if (sessionRef.current !== session) {
    sessionRef.current.active = false;
    sessionRef.current = session;
  }

  const loadDir = useCallback((path: string): Promise<ProjectFileEntry[]> => {
    if (!session.active) return Promise.reject(new Error("Workspace changed"));
    const existing = session.directories.get(path);
    if (existing) return existing;
    const api = getApi();
    if (!api) return Promise.resolve([]);
    const pending = api.list_project_files(path || WORKSPACE_ROOTS_PATH).then((listing) => {
      if (!session.active) throw new Error("Workspace changed");
      return Array.isArray(listing?.entries) ? listing.entries : [];
    }).finally(() => {
      if (session.directories.get(path) === pending) session.directories.delete(path);
    });
    session.directories.set(path, pending);
    return pending;
  }, [session]);

  const reloadTree = useCallback((force = false): Promise<void> => {
    if (session.refresh) {
      if (force) session.refreshAgain = true;
      return session.refresh;
    }
    const api = getApi();
    if (!api || !session.active) return Promise.resolve();
    setError(null);
    const run = async () => {
      let contentEntries: ProjectFileEntry[] | undefined;
      let roots: ProjectFileEntry[] = [];
      const publishContent = () => {
        if (!session.active || !contentEntries) return;
        const contentRoot = roots.find((entry) => entry.read_only === false);
        setCache((prev) => {
          const next = new Map(prev).set("Content", contentEntries!);
          if (contentRoot) next.set(contentRoot.path, contentEntries!);
          return next;
        });
        setLoadingRoot(false);
      };
      // Start Content independently: Verse metadata and other islands cannot gate it.
      const content = loadDir("Content").then((entries) => {
        contentEntries = entries;
        if (!session.active) return;
        if (!roots.length) setRootEntries((prev) => prev.length ? prev : [{ name: "Content", path: "Content", is_dir: true, read_only: false, kind: "content" }]);
        publishContent();
      }).catch((reason: unknown) => {
        if (session.active) {
          setError(reason instanceof Error ? reason.message : "Failed to load Content");
          setLoadingRoot(false);
        }
      });
      const metadata = Promise.all([loadDir(WORKSPACE_ROOTS_PATH), api.list_workspace_roots()]).then(async ([entries, folders]) => {
        if (!session.active) return;
        roots = entries;
        setRootEntries(entries);
        setWorkspaceRoots(Array.isArray(folders) ? folders : []);
        setCache((prev) => new Map(prev).set(WORKSPACE_ROOTS_PATH, entries));
        publishContent();
        const contentPath = entries.find((entry) => entry.read_only === false)?.path;
        const paths = [...new Set([
          ...entries.filter((entry) => entry.read_only && !isProjectContentRoot(entry)).map((entry) => entry.path),
          ...expandedPathsRef.current,
        ])].filter((path) => path !== "Content" && path !== contentPath && path !== WORKSPACE_ROOTS_PATH && isBrowsableTreeDir(path));
        setLoadingPaths(new Set(paths));
        // Bound bridge/disk pressure when restoring a tree with many expanded folders.
        let index = 0;
        const worker = async () => {
          while (session.active && index < paths.length) {
            const path = paths[index++]!;
            try {
              const children = await loadDir(path);
              if (session.active) setCache((prev) => new Map(prev).set(path, children));
            } catch {
              // Missing/deleted core or expanded folders never hide Content.
            } finally {
              if (session.active) setLoadingPaths((prev) => {
                const next = new Set(prev);
                next.delete(path);
                return next;
              });
            }
          }
        };
        await Promise.all(Array.from({ length: Math.min(4, paths.length) }, worker));
      }).catch(() => {
        // A usable Content listing remains visible if optional workspace metadata fails.
      });
      await Promise.all([content, metadata]);
    };
    const refresh = async () => {
      do {
        session.refreshAgain = false;
        await run();
      } while (session.active && session.refreshAgain);
    };
    const pending = refresh().finally(() => {
      if (session.refresh === pending) session.refresh = null;
    });
    session.refresh = pending;
    return pending;
  }, [session, loadDir, expandedPathsRef]);

  useEffect(() => {
    session.active = true;
    setRootEntries([]);
    setWorkspaceRoots([]);
    setCache(new Map());
    setLoadingPaths(new Set());
    setLoadingRoot(true);
    return () => { session.active = false; };
  }, [session]);

  useEffect(() => onApiReady(() => { void reloadTree(); }), [reloadTree, refreshToken]);
  const wasActive = useRef(isActive);
  useEffect(() => {
    if (isActive && !wasActive.current) void reloadTree();
    wasActive.current = isActive;
  }, [isActive, reloadTree]);

  return { rootEntries, workspaceRoots, cache, setCache, error, setError, loadingRoot, loadingPaths, setLoadingPaths, loadDir, reloadTree };
}

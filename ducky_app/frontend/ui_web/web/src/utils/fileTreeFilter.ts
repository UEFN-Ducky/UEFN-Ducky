import type { ProjectFileEntry } from "../types/panel";
import { WORKSPACE_ROOTS_PATH, UEFN_CORE_SECTION_PATH, isAbsEncodedPath, isWsEncodedPath } from "../verse-editor/utils/isVerseFile";
import { ancestorDirPaths, isBrowsableTreeDir, parentDirPath } from "./fileTreeDrag";

export type DirCache = ReadonlyMap<string, readonly ProjectFileEntry[]>;

/** Literal, predictable filter for the sidebar tree — NOT the ranked fuzzy finder
 * that Quick Open uses. Every whitespace-separated word must appear as a substring
 * of the file/folder NAME. Matching the full path with fuzzy subsequence flooded the
 * tree: "player" hit every ".../Gameplay/..." path and "manager" hit every "*Manager*"
 * folder, so an unrelated wall of files showed and the render locked up. */
function matchesQuery(query: string, name: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = name.toLowerCase();
  for (const token of q.split(/\s+/)) {
    if (token && !haystack.includes(token)) return false;
  }
  return true;
}

export function entryNameMatches(query: string, name: string): boolean {
  return matchesQuery(query, name);
}

function addAncestors(path: string, visible: Set<string>) {
  if (isAbsEncodedPath(path) || isWsEncodedPath(path)) {
    visible.add(UEFN_CORE_SECTION_PATH);
    return;
  }
  let current: string | null = path;
  while (current) {
    const parent: string | null = parentDirPath(current);
    if (!parent || parent === current) break;
    if (!isBrowsableTreeDir(parent)) break;
    visible.add(parent);
    current = parent;
  }
}
function walkCachedDirs(dirPath: string, cache: DirCache, query: string, visible: Set<string>) {
  const entries = cache.get(dirPath) ?? [];
  for (const entry of entries) {
    if (!entry.is_dir) continue;
    if (matchesQuery(query, entry.name)) {
      visible.add(entry.path);
      addAncestors(entry.path, visible);
    }
    walkCachedDirs(entry.path, cache, query, visible);
  }
}

/** Paths to show while filtering: matches plus ancestors and matching folder branches. */
export function collectVisibleFilePaths(
  query: string,
  filePaths: readonly { path: string; name: string }[],
  cache: DirCache,
): Set<string> {
  const q = query.trim();
  if (!q) return new Set();

  const visible = new Set<string>();

  for (const file of filePaths) {
    if (!matchesQuery(q, file.name)) continue;
    visible.add(file.path);
    addAncestors(file.path, visible);
  }

  walkCachedDirs(WORKSPACE_ROOTS_PATH, cache, q, visible);

  return visible;
}

/** Directory paths that must be loaded/expanded to reveal filtered matches. */
export function dirPathsToExpand(
  visible: Set<string>,
  knownFilePaths: ReadonlySet<string>,
): string[] {
  const dirs = new Set<string>();
  for (const path of visible) {
    if (path === WORKSPACE_ROOTS_PATH) continue;
    if (knownFilePaths.has(path)) {
      for (const ancestor of ancestorDirPaths(path)) {
        dirs.add(ancestor);
      }
    } else {
      dirs.add(path);
    }
  }  return [...dirs];
}

export function shouldShowFileEntry(
  entry: ProjectFileEntry,
  query: string,
  visiblePaths: Set<string>,
  cache: DirCache,
): boolean {
  const q = query.trim();
  if (!q) return true;
  if (visiblePaths.has(entry.path)) return true;
  if (!entry.is_dir) return false;
  return branchHasVisibleDescendant(entry.path, cache, visiblePaths, q);
}

function branchHasVisibleDescendant(
  dirPath: string,
  cache: DirCache,
  visiblePaths: Set<string>,
  query: string,
): boolean {
  const entries = cache.get(dirPath) ?? [];
  for (const child of entries) {
    if (visiblePaths.has(child.path)) return true;
    if (matchesQuery(query, child.name)) return true;
    if (child.is_dir && branchHasVisibleDescendant(child.path, cache, visiblePaths, query)) return true;
  }
  return false;
}

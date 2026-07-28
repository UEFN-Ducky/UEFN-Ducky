import type { ProjectFileEntry } from "../types/panel";
import {
  ABS_PATH_PREFIX,
  UEFN_CORE_SECTION_PATH,
  WORKSPACE_ROOTS_PATH,
  WS_PATH_PREFIX,
  isAbsEncodedPath,
  isPanelReadOnlyFile,
  isWsEncodedPath,
} from "../verse-editor/utils/isVerseFile";
import type { DropPosition } from "./sidebarTree";

export const CONTENT_ROOT_PATH = "Content";

let workspaceFolderAbsPathsLower: string[] = [];

/** Call when workspace roots load so abs: parent walks stay inside UEFN folders. */
export function setWorkspaceFolderAbsPaths(paths: readonly string[]) {
  workspaceFolderAbsPathsLower = paths
    .map((p) => p.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase())
    .filter(Boolean);
}

function absPathLowerFromEncoded(encoded: string): string {
  return encoded.slice(ABS_PATH_PREFIX.length).replace(/\\/g, "/").replace(/\/$/, "").toLowerCase();
}

function isUnderWorkspaceFolder(absPathLower: string): boolean {
  if (workspaceFolderAbsPathsLower.length === 0) return false;
  return workspaceFolderAbsPathsLower.some(
    (root) => absPathLower === root || absPathLower.startsWith(`${root}/`),
  );
}

/** True when a tree path is safe to pass to list_project_files (not abs:C:/Users etc.). */
export function isBrowsableTreeDir(path: string): boolean {
  const lower = path.toLowerCase();
  if (lower === WORKSPACE_ROOTS_PATH || lower === UEFN_CORE_SECTION_PATH) return false;
  if (lower.startsWith(WS_PATH_PREFIX)) return true;
  if (!lower.startsWith(ABS_PATH_PREFIX)) return true;
  return isUnderWorkspaceFolder(absPathLowerFromEncoded(path));
}

/** Parent directory paths that must be expanded to reveal a project file. */
export function parentDirPath(path: string): string | null {
  const norm = path.replace(/\\/g, "/");
  const lower = norm.toLowerCase();
  if (lower === WORKSPACE_ROOTS_PATH) return null;
  if (lower.startsWith(WS_PATH_PREFIX)) return WORKSPACE_ROOTS_PATH;
  if (lower.startsWith(ABS_PATH_PREFIX)) {
    const absPart = norm.slice(ABS_PATH_PREFIX.length);
    const idx = absPart.lastIndexOf("/");
    if (idx <= 0) return null;
    const parentAbs = absPart.slice(0, idx);
    if (!isUnderWorkspaceFolder(parentAbs.toLowerCase())) return null;
    const parent = `${ABS_PATH_PREFIX}${parentAbs}`;
    return parent === norm ? null : parent;
  }
  if (lower === CONTENT_ROOT_PATH.toLowerCase()) return WORKSPACE_ROOTS_PATH;
  const idx = norm.lastIndexOf("/");
  if (idx <= 0) return WORKSPACE_ROOTS_PATH;
  return norm.slice(0, idx);
}

export function ancestorDirPaths(filePath: string): string[] {
  const paths: string[] = [];
  let current: string | null = filePath.replace(/\\/g, "/");
  while (current) {
    const parent = parentDirPath(current);
    if (!parent || parent === current) break;
    paths.push(parent);
    current = parent;
  }
  return paths;
}

export type FileEntryKind = "dir" | "file";

export type DirListing = ReadonlyMap<string, readonly ProjectFileEntry[]>;

export function treePathDepth(path: string): number {
  const norm = path.replace(/\\/g, "/");
  if (norm === UEFN_CORE_SECTION_PATH) return -1;
  if (norm === WORKSPACE_ROOTS_PATH) return 0;
  if (norm.toLowerCase().startsWith(WS_PATH_PREFIX)) return 1;
  if (norm.toLowerCase().startsWith(ABS_PATH_PREFIX)) {
    const segments = norm.slice(ABS_PATH_PREFIX.length).split("/").filter(Boolean);
    return 1 + Math.max(0, segments.length - 1);
  }
  if (norm === CONTENT_ROOT_PATH) return 1;
  const prefix = `${CONTENT_ROOT_PATH}/`;
  if (!norm.startsWith(prefix)) {
    const segments = norm.split("/").filter(Boolean);
    return Math.max(1, segments.length);
  }
  return 1 + norm.slice(prefix.length).split("/").filter(Boolean).length;
}

export function collapseExpandedPathsOneLevel(expandedPaths: ReadonlySet<string>): Set<string> {
  const paths = [...expandedPaths].filter((path) => path !== UEFN_CORE_SECTION_PATH);
  if (paths.length === 0) return new Set();
  const maxDepth = Math.max(...paths.map(treePathDepth));
  const next = new Set(expandedPaths);
  for (const path of paths) {
    if (treePathDepth(path) === maxDepth) next.delete(path);
  }
  return next;
}

export function expandExpandedPathsOneLevel(
  expandedPaths: ReadonlySet<string>,
  cache: DirListing,
  rootEntries: readonly ProjectFileEntry[],
): Set<string> {
  const contentPaths = [...expandedPaths].filter((path) => path !== UEFN_CORE_SECTION_PATH);
  const maxDepth =
    contentPaths.length === 0 ? 0 : Math.max(...contentPaths.map(treePathDepth));
  const targetDepth = maxDepth + 1;
  const next = new Set(expandedPaths);

  if (targetDepth === 1) {
    for (const entry of rootEntries) {
      if (entry.is_dir) next.add(entry.path);
    }
    return next;
  }

  const visitExpanded = (entries: readonly ProjectFileEntry[]) => {
    for (const entry of entries) {
      if (!entry.is_dir || !expandedPaths.has(entry.path)) continue;
      const children = cache.get(entry.path) ?? [];
      for (const child of children) {
        if (child.is_dir && treePathDepth(child.path) === targetDepth) {
          next.add(child.path);
        }
      }
      visitExpanded(children);
    }
  };
  visitExpanded(rootEntries);
  return next;
}

export function fileDragId(entry: Pick<ProjectFileEntry, "path" | "is_dir">): string {
  return entry.is_dir ? `dir:${entry.path}` : `file:${entry.path}`;
}

export function parseFileDragId(raw: string): { kind: FileEntryKind; path: string } | null {
  const idx = raw.indexOf(":");
  if (idx <= 0) return null;
  const kind = raw.slice(0, idx) as FileEntryKind;
  if (kind !== "dir" && kind !== "file") return null;
  return { kind, path: raw.slice(idx + 1) };
}

export function fileNestDropId(dirPath: string): string {
  return `nest:${dirPath}`;
}

export function parseFileNestDropId(raw: string): string | null {
  if (!raw.startsWith("nest:")) return null;
  return raw.slice(5);
}

export function parentPath(path: string): string {
  return parentDirPath(path) ?? WORKSPACE_ROOTS_PATH;
}

export function isDescendantDir(ancestorPath: string, candidatePath: string): boolean {
  const ancestor = ancestorPath.replace(/\\/g, "/");
  const candidate = candidatePath.replace(/\\/g, "/");
  return candidate === ancestor || candidate.startsWith(`${ancestor}/`);
}

export function resolveFileDragOverId(
  cache: Map<string, ProjectFileEntry[]>,
  overRaw: string,
  position: DropPosition,
): string {
  if (position === "inside") {
    const parsed = parseFileDragId(overRaw);
    if (parsed?.kind === "dir") return fileNestDropId(parsed.path);
    return overRaw;
  }

  const parsed = parseFileDragId(overRaw);
  if (!parsed) return overRaw;

  // before/after a directory = sibling slot in its parent (line), never into it.
  if (parsed.kind === "dir") {
    return fileNestDropId(parentPath(parsed.path));
  }

  if (position !== "after") return overRaw;

  const siblings = cache.get(parentPath(parsed.path)) ?? [];
  const idx = siblings.findIndex((entry) => entry.path === parsed.path);
  const next = siblings[idx + 1];
  // Next dir would ingest via file→dir resolve — keep parent nest for between slots.
  if (next && !next.is_dir) return fileDragId(next);
  return fileNestDropId(parentPath(parsed.path));
}

/** Resolve a drag-over target to a destination parent folder path, or null if no move. */
export function resolveFileMoveTarget(
  sourcePath: string,
  sourceIsDir: boolean,
  overRaw: string,
): string | null {
  if (isPanelReadOnlyFile(sourcePath)) return null;

  const nestTarget = parseFileNestDropId(overRaw);
  if (nestTarget !== null) {
    if (isPanelReadOnlyFile(nestTarget)) return null;
    const destParent = nestTarget || CONTENT_ROOT_PATH;
    if (sourceIsDir && (destParent === sourcePath || isDescendantDir(sourcePath, destParent))) return null;
    if (parentPath(sourcePath) === destParent) return null;
    return destParent;
  }

  const active = { kind: sourceIsDir ? ("dir" as const) : ("file" as const), path: sourcePath };
  const over = parseFileDragId(overRaw);
  if (!over) return null;
  if (isPanelReadOnlyFile(over.path)) return null;

  if (active.kind === "dir" && over.kind === "dir") {
    if (active.path === over.path || isDescendantDir(active.path, over.path)) return null;
    if (parentPath(active.path) === parentPath(over.path)) return null;
    return over.path;
  }

  if (active.kind === "file" && over.kind === "file") {
    if (parentPath(active.path) === parentPath(over.path)) return null;
    return parentPath(over.path);
  }

  if (active.kind === "file" && over.kind === "dir") {
    if (parentPath(active.path) === over.path) return null;
    return over.path;
  }

  if (active.kind === "dir" && over.kind === "file") {
    const destParent = parentPath(over.path);
    if (isPanelReadOnlyFile(destParent)) return null;
    if (isDescendantDir(active.path, destParent)) return null;
    if (parentPath(active.path) === destParent) return null;
    return destParent;
  }

  return null;
}

/** Map an abs: file path to its ws: workspace root id when possible. */
export function workspaceRootIdForAbsPath(
  absEncodedPath: string,
  roots: ReadonlyArray<{ id: string; path: string }>,
): string | null {
  const lower = absEncodedPath.toLowerCase();
  if (!lower.startsWith(ABS_PATH_PREFIX)) return null;
  const fileAbs = absEncodedPath.slice(ABS_PATH_PREFIX.length).replace(/\\/g, "/").toLowerCase();
  for (const root of roots) {
    const rootPath = root.path.replace(/\\/g, "/").toLowerCase();
    if (fileAbs === rootPath || fileAbs.startsWith(`${rootPath}/`)) {
      return root.id;
    }
  }
  return null;
}

/** Directory paths to expand when revealing a file (includes ws: root for abs: files). */
export function revealDirPaths(
  filePath: string,
  roots: ReadonlyArray<{ id: string; path: string }>,
): string[] {
  const norm = filePath.replace(/\\/g, "/");
  if (isAbsEncodedPath(norm) || isWsEncodedPath(norm)) {
    const dirs = new Set<string>([UEFN_CORE_SECTION_PATH]);
    const wsRoot = workspaceRootIdForAbsPath(norm, roots) ?? (isWsEncodedPath(norm) ? norm : null);
    if (wsRoot) dirs.add(wsRoot);
    return [...dirs];
  }
  const dirs = new Set<string>();
  for (const p of ancestorDirPaths(filePath)) {
    if (isBrowsableTreeDir(p)) dirs.add(p);
  }
  dirs.add(WORKSPACE_ROOTS_PATH);
  return [...dirs];
}

export function isWorkspaceTreeRoot(path: string): boolean {
  return isWsEncodedPath(path) || path === WORKSPACE_ROOTS_PATH;
}

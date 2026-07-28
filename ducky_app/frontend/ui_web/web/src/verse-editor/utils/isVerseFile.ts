import {
  EDITABLE_TEXT_EXTENSIONS_FULL,
  classifyFilePath,
  isEditableTextFilePath,
  isImageFilePath,
  isKnownTextFilename,
  isModelFilePath,
  isUnrealAssetPath,
  monacoLanguageForFileKind,
} from "./fileKind";

const VERSE_EXTENSIONS = [".verse", ".versetest", ".vson"];

export {
  classifyFilePath,
  isEditableTextFilePath,
  isImageFilePath,
  isKnownTextFilename,
  isModelFilePath,
  isUnrealAssetPath,
};

export const EDITABLE_TEXT_EXTENSIONS = [
  ...VERSE_EXTENSIONS,
  ".py",
  ".txt",
  ".json",
  ".md",
  ".cfg",
  ".ini",
  ".toml",
  ".yaml",
  ".yml",
  ".xml",
  ".csv",
  ".log",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".css",
  ".scss",
  ".less",
  ".html",
  ".htm",
  ".rs",
  ".php",
  ".c",
  ".h",
  ".cpp",
  ".cxx",
  ".cc",
  ".hpp",
  ".hh",
  ".go",
] as const;

const BINARY_PROJECT_SUFFIXES = [
  ".umap",
  ".uasset",
  ".ubulk",
  ".uexp",
  ".uptod",
  ".udic",
  ".ufont",
  ".ushaderbytecode",
  ".upipelinecache",
];

export const WORKSPACE_ROOTS_PATH = "__roots__";
export const UEFN_CORE_SECTION_PATH = "__uefn_core__";
export const WS_PATH_PREFIX = "ws:";
export const ABS_PATH_PREFIX = "abs:";
/** A file the user dragged in from Explorer, opened read-only in place (not imported).
 * Like abs: but ungated — treated as read-only everywhere abs: is. */
export const EXT_PATH_PREFIX = "ext:";

export function isVerseFile(relativePath: string): boolean {
  const lower = relativePath.toLowerCase();
  return VERSE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export function isPythonFile(relativePath: string): boolean {
  return relativePath.toLowerCase().endsWith(".py");
}

export function isEditableTextFile(relativePath: string): boolean {
  if (isPanelReadOnlyFile(relativePath) && !isExtEncodedPath(relativePath)) return false;
  if (isBinaryProjectFile(relativePath)) return false;
  if (isImageFilePath(relativePath)) return false;
  if (isModelFilePath(relativePath)) return false;
  return isEditableTextFilePath(relativePath) || isKnownTextFilename(basename(relativePath));
}

const MONACO_LANGUAGE_BY_EXT: Record<string, string> = {
  ".py": "python",
  ".json": "json",
  ".md": "markdown",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".xml": "xml",
  ".csv": "plaintext",
  ".ini": "ini",
  ".toml": "ini",
  ".cfg": "ini",
  ".log": "plaintext",
  ".txt": "plaintext",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".js": "javascript",
  ".jsx": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".css": "css",
  ".scss": "scss",
  ".less": "less",
  ".html": "html",
  ".htm": "html",
  ".rs": "rust",
  ".php": "php",
  ".c": "c",
  ".h": "c",
  ".cpp": "cpp",
  ".cxx": "cpp",
  ".cc": "cpp",
  ".hpp": "cpp",
  ".hh": "cpp",
  ".go": "go",
};

export function monacoLanguageForPath(relativePath: string): string {
  const override = monacoLanguageForFileKind(relativePath);
  if (override) return override;
  const lower = relativePath.toLowerCase();
  for (const [ext, lang] of Object.entries(MONACO_LANGUAGE_BY_EXT)) {
    if (lower.endsWith(ext)) return lang;
  }
  if (isVerseFile(relativePath)) return "verse";
  // Known text names / editable extensions from the expanded set
  for (const ext of EDITABLE_TEXT_EXTENSIONS_FULL) {
    if (lower.endsWith(ext) && !(ext in MONACO_LANGUAGE_BY_EXT)) return "plaintext";
  }
  return "plaintext";
}

export function isBinaryProjectFile(relativePath: string): boolean {
  const lower = relativePath.toLowerCase();
  return BINARY_PROJECT_SUFFIXES.some((ext) => lower.endsWith(ext));
}

export function isAbsEncodedPath(path: string): boolean {
  return normPath(path).startsWith(ABS_PATH_PREFIX);
}

export function isExtEncodedPath(path: string): boolean {
  return normPath(path).startsWith(EXT_PATH_PREFIX);
}

export function isWsEncodedPath(path: string): boolean {
  return normPath(path).startsWith(WS_PATH_PREFIX);
}

export function isDigestFile(relativePath: string): boolean {
  const lower = normPath(relativePath);
  return lower.includes(".digest.verse") || lower.endsWith(".digest.verse");
}

/** Any path outside Content/ — locked in Ducky (digest, Assets, vproject, etc.). */
export function isWorkspaceLockedFile(relativePath: string): boolean {
  const lower = normPath(relativePath).replace(/^\/+/, "");
  if (lower === WORKSPACE_ROOTS_PATH) return true;
  if (lower.startsWith(WS_PATH_PREFIX)) return true;
  if (lower.startsWith(ABS_PATH_PREFIX)) return true;
  // ext: = a dragged-in external file opened for editing in place — NOT locked.
  if (lower.startsWith(EXT_PATH_PREFIX)) return false;
  return !lower.startsWith("content/") && lower !== "content";
}

/** Deployed listener bootstrap — visible but never editable in-panel. */
export function isLockedProjectFile(relativePath: string): boolean {
  const norm = normPath(relativePath).replace(/^\/+/, "").toLowerCase();
  return norm === "content/python/init_unreal.py";
}

/** True when Ducky must not edit (locked bootstrap or workspace/digest files). */
export function isPanelReadOnlyFile(relativePath: string): boolean {
  return isLockedProjectFile(relativePath) || isWorkspaceLockedFile(relativePath);
}

export function isWritableContentPath(path: string): boolean {
  return !isWorkspaceLockedFile(path);
}

export function basename(path: string): string {
  const norm = path.replace(/\\/g, "/");
  if (isAbsEncodedPath(norm) || isExtEncodedPath(norm)) {
    // ABS_PATH_PREFIX and EXT_PATH_PREFIX are both 4 chars ("abs:"/"ext:").
    const absPart = norm.slice(ABS_PATH_PREFIX.length);
    const parts = absPart.split("/").filter(Boolean);
    return parts[parts.length - 1] || absPart;
  }
  const parts = norm.split("/");
  return parts[parts.length - 1] || norm;
}

/** Canonical identity key for a project path. LOWERCASED: Windows paths are
 * case-insensitive and the same file reaches identity maps (editors, reveals, dirty
 * state, vim) with on-disk casing from the tree AND lowercase registry keys
 * from the Problems panel — they must collide. Never use for display; tabs carry their
 * own display name. */
export function normPath(path: string): string {
  return path.replace(/\\/g, "/").toLowerCase();
}

/** Canonical registry key — Content-relative or abs:/ws: workspace paths. */
export function registryKey(path: string): string {
  const p = normPath(path).replace(/^\/+/, "");
  if (
    p.startsWith(ABS_PATH_PREFIX) ||
    p.startsWith(EXT_PATH_PREFIX) ||
    p.startsWith(WS_PATH_PREFIX) ||
    p === WORKSPACE_ROOTS_PATH
  ) {
    return p;
  }
  if (!p.startsWith("content/")) {
    return `content/${p}`;
  }
  return p;
}

/** Project-relative path with Content/ prefix (preserves on-disk segment casing). */
export function projectRelativePath(path: string): string {
  let p = path.replace(/\\/g, "/").replace(/^\/+/, "");
  const lower = p.toLowerCase();
  if (
    lower.startsWith(ABS_PATH_PREFIX) ||
    lower.startsWith(EXT_PATH_PREFIX) ||
    lower.startsWith(WS_PATH_PREFIX) ||
    lower === WORKSPACE_ROOTS_PATH
  ) {
    return p;
  }
  if (!lower.startsWith("content/")) {
    p = `Content/${p}`;
  }
  return p;
}

/** Lookup keys for a tab path (handles legacy keys without Content/ prefix). */
export function registryLookupKeys(path: string): string[] {
  const canonical = registryKey(path);
  if (
    canonical.startsWith(ABS_PATH_PREFIX) ||
    canonical.startsWith(EXT_PATH_PREFIX) ||
    canonical.startsWith(WS_PATH_PREFIX)
  ) {
    return [canonical];
  }
  const stripped = canonical.startsWith("content/") ? canonical.slice("content/".length) : canonical;
  if (stripped === canonical) return [canonical];
  return [canonical, stripped];
}

/** VS Code-style red label for system-managed workspace roots. */
export function isSystemWorkspaceRootName(name: string): boolean {
  const n = name.trim();
  return n === "vproject - DO NOT MODIFY" || n === "/Verse.org";
}

/** Friendly sidebar label for a UEFN multi-root workspace folder (paths unchanged). */
export function workspaceRootDisplayName(entry: { name: string; read_only?: boolean }): string {
  if (entry.read_only === false) return "Content";
  const name = entry.name.trim();
  if (name === "vproject - DO NOT MODIFY") return "vproject";
  if (/\(Assets\)\s*$/i.test(name)) return "Assets";
  if (name.startsWith("/")) return name.slice(1);
  const slash = name.lastIndexOf("/");
  if (slash >= 0) {
    const tail = name.slice(slash + 1).trim();
    if (tail) return tail.replace(/\s*\(Assets\)\s*$/i, "Assets").trim() || tail;
  }
  return name;
}

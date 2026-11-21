import { isFolderProject } from "../../verse-editor/utils/isVerseFile";

const WORKSPACE_PATH_RE =
  /^(?:content\/)?(?:verse|textures|materials|blueprints|audio|meshes|ui|plugins)[/\\]/i;

const FILE_EXT_RE = /\.(verse|versetest|vson|uasset|umap|py|json|md|txt|toml|cfg)$/i;

export function isWorkspaceFilePath(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed || trimmed.includes("://")) return false;
  if (WORKSPACE_PATH_RE.test(trimmed)) return true;
  if (FILE_EXT_RE.test(trimmed)) return true;
  if (trimmed.startsWith("Verse/") || trimmed.startsWith("verse/")) return true;
  return false;
}

/** A file or folder on this PC by full path: `file:///C:/a/b.mp4`, `C:/a/b.mp4` or `C:\a\b.mp4`
 * (percent-escapes decoded). Returns the Windows path, or null for anything else. */
export function localFilePath(href: string): string | null {
  let s = (href || "").trim().replace(/^file:\/\/\/?/i, "");
  try {
    s = decodeURIComponent(s);
  } catch {
    return null;
  }
  return /^[A-Za-z]:[\\/]/.test(s) ? s.replace(/\//g, "\\") : null;
}

export function normalizeWorkspacePath(path: string): string {
  let p = path.replace(/\\/g, "/").trim();
  if (p.startsWith("/")) return p;
  if (isFolderProject()) return p.replace(/^\.\//, "");
  if (/^[^/]+\.verse$/i.test(p)) p = `Verse/${p}`;
  if (!p.toLowerCase().startsWith("content/")) {
    p = `Content/${p.replace(/^\/+/, "")}`;
  }
  return p;
}

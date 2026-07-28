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

export function normalizeWorkspacePath(path: string): string {
  let p = path.replace(/\\/g, "/").trim();
  if (!p.toLowerCase().startsWith("content/")) {
    p = `Content/${p.replace(/^\/+/, "")}`;
  }
  return p;
}

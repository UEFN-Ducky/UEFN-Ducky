/**
 * Where desktop-owned assets live, as seen by the page.
 *
 * On the desktop the panel is served at the origin root, so `/plugin-ui/x` is
 * correct. In direct mode the same bundle is served from a subdirectory of the
 * site (`/static/plugins/uefn-ducky/panel/`), where a root-relative path would
 * hit the site instead — and, worse, would sit outside the Service Worker's
 * scope, so nothing could serve it from the desktop.
 *
 * Everything that builds a desktop asset URL goes through `assetUrl` so both
 * modes resolve, and so every such URL stays inside the worker's scope.
 */

/** Directory the panel document was served from. "/" on the desktop. */
function documentDir(): string {
  if (typeof document === "undefined") return "/";
  try {
    const path = new URL(".", document.baseURI).pathname;
    return path.endsWith("/") ? path : `${path}/`;
  } catch {
    return "/";
  }
}

let cached: string | null = null;

/**
 * Base for desktop assets, always ending in "/". Resolved once at boot —
 * the panel pushes history state, so re-deriving it later would drift.
 */
export function assetBase(): string {
  if (cached === null) cached = documentDir();
  return cached;
}

/** True when the panel is served from a subdirectory (direct mode). */
export function isSubdirectoryPanel(): boolean {
  return assetBase() !== "/";
}

/**
 * Absolute URL for a desktop-owned asset path. Accepts either "plugin-ui/x"
 * or "/plugin-ui/x"; leaves full URLs and data/blob URLs untouched.
 */
export function assetUrl(path: string): string {
  const raw = String(path || "");
  if (!raw) return raw;
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith("//")) return raw;
  return assetBase() + raw.replace(/^\/+/, "");
}

/** Test seam: pin the base instead of reading the document. */
export function __setAssetBaseForTest(base: string | null): void {
  cached = base;
}

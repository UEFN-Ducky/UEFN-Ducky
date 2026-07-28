/** True when running in a Windows browser context. */
function isWindowsPlatform(): boolean {
  return typeof navigator !== "undefined" && /Win/i.test(navigator.platform);
}

/** Case-fold a filesystem path segment for URI comparison on Windows. */
function caseFoldPathname(pathname: string): string {
  if (/^[a-zA-Z]:/.test(pathname)) {
    return pathname[0].toUpperCase() + pathname.slice(1).toLowerCase();
  }
  if (isWindowsPlatform()) {
    return pathname.toLowerCase();
  }
  return pathname;
}

/** Build a Windows drive path from URL parts (handles file:///C:/x and file:///C%3A/x). */
function windowsPathFromFileUrl(parsed: URL): string {
  let pathname = decodeURIComponent(parsed.pathname).replace(/\\/g, "/");
  if (/^\/[a-zA-Z]:/.test(pathname)) {
    return pathname.slice(1);
  }
  if (/^[a-zA-Z]$/.test(parsed.hostname)) {
    const rest = pathname.startsWith("/") ? pathname : `/${pathname}`;
    return `${parsed.hostname.toUpperCase()}:${rest}`;
  }
  return pathname.replace(/^\/+/, "");
}

/** Normalize file URIs for comparison (Windows drive letter, slashes, encoding, case). */
export function normalizeFileUri(uri: string): string {
  try {
    const parsed = new URL(uri);
    let pathname = windowsPathFromFileUrl(parsed);
    pathname = caseFoldPathname(pathname.replace(/^\/+/, ""));
    return `file:///${pathname}`;
  } catch {
    return uri.replace(/\\/g, "/");
  }
}

export function fileUrisEqual(a: string, b: string): boolean {
  return normalizeFileUri(a) === normalizeFileUri(b);
}

/** URI match with relative-path fallback when project root is known. */
export function fileUrisMatch(a: string, b: string, projectRoot?: string): boolean {
  if (fileUrisEqual(a, b)) return true;
  if (!projectRoot) return false;
  const relA = fileUriToRelativePath(projectRoot, a);
  const relB = fileUriToRelativePath(projectRoot, b);
  if (!relA || !relB) return false;
  return relA.toLowerCase() === relB.toLowerCase();
}

/** Absolute filesystem path (project root + relative path, or abs: workspace path). */
export function toEditorAbsolutePath(projectRoot: string, relativePath: string): string {
  const input = relativePath.replace(/\\/g, "/");
  // abs: and ext: both encode a full absolute path after a 4-char prefix.
  if (input.toLowerCase().startsWith("abs:") || input.toLowerCase().startsWith("ext:")) {
    return input.slice(4);
  }
  return toAbsolutePath(projectRoot, input);
}

/** Absolute filesystem path (project root + relative path). */
export function toAbsolutePath(projectRoot: string, relativePath: string): string {
  const root = projectRoot.replace(/\\/g, "/").replace(/\/+$/, "");
  const rel = relativePath.replace(/\\/g, "/").replace(/^\/+/, "");
  return rel ? `${root}/${rel}` : root;
}

/** Canonical file URI for LSP wire format — preserves path casing (do not case-fold). */
export function toFileUri(projectRoot: string, relativePath: string): string {
  const full = toAbsolutePath(projectRoot, relativePath).replace(/\\/g, "/");
  if (/^[a-zA-Z]:/.test(full)) {
    return `file:///${full}`;
  }
  return `file://${full.startsWith("/") ? "" : "/"}${full}`;
}

/** Epic VS Code code2Protocol — file:///c%3A/... on Windows (matches Python to_monaco_file_uri). */
export function toMonacoProtocolUriFromAbs(absPath: string): string {
  const full = absPath.replace(/\\/g, "/");
  const encodeSegs = (pathPart: string) =>
    pathPart
      .split("/")
      .map((seg) => encodeURIComponent(seg))
      .join("/");
  if (/^[a-zA-Z]:/.test(full)) {
    const drive = full[0].toLowerCase();
    const rest = full.slice(2);
    return `file:///${encodeSegs(`${drive}:${rest}`)}`;
  }
  return `file:///${encodeSegs(full.replace(/^\/+/, ""))}`;
}

/** Outbound LSP document URI — Epic code2Protocol: pass Monaco/VS Code Uri.toString() through unchanged. */
export function toLspProtocolUri(
  projectRoot: string,
  uriOrRelative: string,
  monaco?: typeof import("monaco-editor") | null,
): string {
  const input = uriOrRelative.trim();
  if (input.includes("://")) {
    return input;
  }
  const abs = toEditorAbsolutePath(projectRoot, input);
  return monaco ? monaco.Uri.file(abs).toString() : toMonacoProtocolUriFromAbs(abs);
}

/** @deprecated Use toLspProtocolUri — kept for exports */
export function toLspWireUri(projectRoot: string, uriOrRelative: string): string {
  return toLspProtocolUri(projectRoot, uriOrRelative);
}

/** Resolve a file URI to panel path (Content/..., abs:..., or null). */
export function resolveWorkspaceFilePath(
  projectRoot: string,
  workspaceFolderPaths: readonly string[],
  uri: string,
): string | null {
  try {
    const uriAbs = windowsPathFromFileUrl(new URL(uri)).replace(/\\/g, "/");
    const uriKey = uriAbs.toLowerCase();
    const roots =
      workspaceFolderPaths.length > 0
        ? workspaceFolderPaths
        : [toAbsolutePath(projectRoot, "Content"), projectRoot];
    let bestRoot = "";
    for (const root of roots) {
      const rootAbs = root.replace(/\\/g, "/").replace(/\/+$/, "");
      const rootKey = rootAbs.toLowerCase();
      if (uriKey === rootKey || uriKey.startsWith(`${rootKey}/`)) {
        if (rootAbs.length > bestRoot.length) bestRoot = rootAbs;
      }
    }
    if (!bestRoot) {
      const legacy = fileUriToRelativePathPreserveCaseUnderRoot(projectRoot, uri);
      return legacy;
    }
    const contentRoot = toAbsolutePath(projectRoot, "Content").replace(/\\/g, "/");
    if (bestRoot.toLowerCase() === contentRoot.toLowerCase()) {
      const suffix = uriAbs.slice(bestRoot.length).replace(/^\/+/, "");
      return suffix ? `Content/${suffix}` : "Content";
    }
    return `abs:${uriAbs}`;
  } catch {
    return fileUriToRelativePathPreserveCaseUnderRoot(projectRoot, uri);
  }
}

function fileUriToRelativePathPreserveCaseUnderRoot(projectRoot: string, uri: string): string | null {
  try {
    const rootAbs = toAbsolutePath(projectRoot, "").replace(/\\/g, "/");
    const uriAbs = windowsPathFromFileUrl(new URL(uri)).replace(/\\/g, "/");
    const rootKey = rootAbs.toLowerCase();
    const uriKey = uriAbs.toLowerCase();
    if (!uriKey.startsWith(rootKey)) return null;
    const suffix = uriAbs.slice(rootAbs.length).replace(/^\/+/, "");
    return suffix || null;
  } catch {
    return null;
  }
}

/** Relative path from a file URI under project root (case-preserved from the URI path). */
export function fileUriToRelativePathPreserveCase(projectRoot: string, uri: string): string | null {
  return resolveWorkspaceFilePath(projectRoot, [], uri);
}

export function fileUriToRelativePath(projectRoot: string, uri: string): string | null {
  const preserved = fileUriToRelativePathPreserveCase(projectRoot, uri);
  if (!preserved) return null;
  return preserved.toLowerCase();
}

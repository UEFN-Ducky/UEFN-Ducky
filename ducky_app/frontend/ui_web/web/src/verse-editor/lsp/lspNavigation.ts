import { basename } from "../utils/isVerseFile";
import { fileUriToRelativePathPreserveCase, fileUrisMatch } from "./uriUtils";
import type { RevealRequest } from "./registerLspProviders";

export type LspNavRange = {
  startLineNumber: number;
  startColumn: number;
};

export type LspNavLocation = {
  uri: string;
  range: LspNavRange;
};

export type NavigateToFile = (req: RevealRequest, options?: { activate?: boolean }) => void;

export function lspLocationToReveal(projectRoot: string, loc: LspNavLocation): RevealRequest | null {
  const rel = fileUriToRelativePathPreserveCase(projectRoot, loc.uri);
  if (!rel) return null;
  return {
    path: rel,
    line: loc.range.startLineNumber,
    column: loc.range.startColumn,
  };
}

export function isProjectFileUri(projectRoot: string, uri: string, docUri: string): boolean {
  return fileUrisMatch(uri, docUri, projectRoot);
}

/** Open editor tab(s) for LSP locations; first match is focused, others open in background. */
export function navigateToLspLocations(
  projectRoot: string,
  docUri: string,
  locations: LspNavLocation[],
  navigate: NavigateToFile,
): boolean {
  const seen = new Set<string>();
  const targets: RevealRequest[] = [];

  for (const loc of locations) {
    if (isProjectFileUri(projectRoot, loc.uri, docUri)) continue;
    const req = lspLocationToReveal(projectRoot, loc);
    if (!req) continue;
    const key = `${req.path}:${req.line}:${req.column}`;
    if (seen.has(key)) continue;
    seen.add(key);
    targets.push(req);
  }

  if (!targets.length) return false;

  targets.forEach((req, index) => {
    navigate(req, { activate: index === 0 });
  });
  return true;
}

export function revealRequestName(req: RevealRequest): string {
  return basename(req.path);
}

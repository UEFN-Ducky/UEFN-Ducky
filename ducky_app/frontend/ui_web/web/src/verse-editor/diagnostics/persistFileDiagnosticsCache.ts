import { getApi } from "../../hooks/usePanelApi";
import { isVerseDiagnosticsAutoCheckEnabled, isVerseDiagnosticsCacheEnabled } from "./verseDiagnosticsSettings";
import { countDiagnosticSeverities, lspDiagnosticsToItems } from "../lsp/diagnosticCounts";
import type { LspDiagnostic } from "../lsp/verseLspClient";
import { fileUriToRelativePath } from "../lsp/uriUtils";

/** Persist live LSP diagnostics for one file into the project cache JSON. */
export function persistFileDiagnosticsCache(
  projectRoot: string,
  lspUri: string,
  diagnostics: LspDiagnostic[],
): void {
  if (!isVerseDiagnosticsCacheEnabled()) return;
  const rel = fileUriToRelativePath(projectRoot, lspUri);
  if (!rel || !rel.toLowerCase().endsWith(".verse")) return;
  const api = getApi();
  if (!api) return;
  const counts = countDiagnosticSeverities(diagnostics);
  const items = lspDiagnosticsToItems(diagnostics);
  void api.save_verse_file_cache(rel, counts.errors, counts.warnings, items, projectRoot);
}

let cacheSaveTimer: ReturnType<typeof setTimeout> | null = null;
let pendingSave: { projectRoot: string; uri: string; diagnostics: LspDiagnostic[] } | null = null;

/** Debounced cache write while editing (2s after last diagnostic push). */
export function schedulePersistFileDiagnosticsCache(
  projectRoot: string,
  lspUri: string,
  diagnostics: LspDiagnostic[],
): void {
  if (!isVerseDiagnosticsAutoCheckEnabled() || !isVerseDiagnosticsCacheEnabled()) return;
  pendingSave = { projectRoot, uri: lspUri, diagnostics };
  if (cacheSaveTimer) clearTimeout(cacheSaveTimer);
  cacheSaveTimer = setTimeout(() => {
    cacheSaveTimer = null;
    const pending = pendingSave;
    pendingSave = null;
    if (!pending) return;
    persistFileDiagnosticsCache(pending.projectRoot, pending.uri, pending.diagnostics);
  }, 2000);
}

import { getApi } from "../../hooks/usePanelApi";
import { stopLsp } from "../api/verseEditorApi";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";
import { releaseVerseLspSession } from "../lsp/verseLspSession";
import { verseLspLogError } from "../lsp/verseLspDebug";
import { cancelAllOpenFileDiagnosticRefreshes } from "./refreshOpenFileDiagnostics";

type ScanFilePayload = {
  path: string;
  errors: number;
  warnings: number;
  items?: Array<{ line: number; column: number; message: string; severity: string }>;
};

async function restoreDiagnosticsCache(projectPath: string): Promise<void> {
  const api = getApi();
  if (!api) return;
  try {
    const cached = await api.load_verse_diagnostics_cache(projectPath);
    if (Array.isArray(cached.files) && cached.files.length > 0) {
      fileDiagnosticRegistry.mergeScanResults(cached.files as ScanFilePayload[]);
    }
  } catch {
    // ignore
  }
}

/** Kick off a background project scan (results stream via agent events). */
export function requestProjectDiagnosticsScan(
  projectPath: string,
  options?: { full?: boolean; rootChanged?: boolean },
): number | null {
  const path = projectPath.trim();
  if (!path) return null;

  const full = options?.full ?? false;

  if (options?.rootChanged) {
    releaseVerseLspSession();
    void stopLsp();
  }

  cancelAllOpenFileDiagnosticRefreshes();
  const scanId = fileDiagnosticRegistry.beginScan(full);

  const api = getApi();
  if (!api) {
    fileDiagnosticRegistry.endScan(scanId);
    return null;
  }

  void api
    .scan_verse_diagnostics(path, full)
    .then((result) => {
      // Scans always run in the backend's ephemeral verse-lsp (separate process) —
      // progress streams in via agent events. Scanning through the editor's live
      // session starved every interactive LSP request and locked the editor up.
      const pending = (result as { pending?: boolean }).pending;
      if (pending) return;
      if (Array.isArray(result.files)) {
        if (full) {
          fileDiagnosticRegistry.setScanResults(result.files as ScanFilePayload[], scanId);
        } else {
          fileDiagnosticRegistry.mergeScanResults(result.files as ScanFilePayload[], scanId);
        }
      } else {
        fileDiagnosticRegistry.endScan(scanId);
      }
    })
    .catch((err) => {
      verseLspLogError("scan", "project diagnostics scan failed", err);
      fileDiagnosticRegistry.endScan(scanId);
      void restoreDiagnosticsCache(path);
    });

  return scanId;
}

export function normProjectRoot(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

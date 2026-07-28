import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";
import { fileUriToRelativePath } from "../lsp/uriUtils";
import { getVerseLspSession } from "../lsp/verseLspSession";
import { registryKey } from "../utils/isVerseFile";
import { persistFileDiagnosticsCache } from "./persistFileDiagnosticsCache";
import { isVerseDiagnosticsAutoCheckEnabled } from "./verseDiagnosticsSettings";
import { refreshOpenFileDiagnostics } from "./refreshOpenFileDiagnostics";
import { requestProjectDiagnosticsScan } from "./requestProjectScan";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** After agent finishes writing a .verse file, sync LSP and update the problems panel. */
export async function checkFileAfterAgentWrite(
  projectPath: string,
  relativePath: string,
  getEditorText?: (path: string) => string | undefined,
): Promise<void> {
  const root = projectPath.trim();
  const path = relativePath.trim();
  if (!root || !path.toLowerCase().endsWith(".verse")) return;
  if (!isVerseDiagnosticsAutoCheckEnabled()) return;

  const syncAndPull = async (): Promise<boolean> => {
    const session = getVerseLspSession();
    if (!session?.client.isConnected()) return false;

    const relKey = registryKey(path);
    let docUri = "";
    let text = getEditorText?.(path);

    for (const model of session.models.values()) {
      if (model.isDisposed()) continue;
      const modelUri = model.uri.toString();
      const rel = fileUriToRelativePath(session.projectRoot, modelUri);
      if (rel && registryKey(rel) === relKey) {
        docUri = modelUri;
        if (text === undefined) text = model.getValue();
        break;
      }
    }

    if (!docUri || text === undefined) {
      return refreshOpenFileDiagnostics(root, path);
    }

    session.client.openDocument(docUri, text);
    session.client.flushChanges(docUri);
    await sleep(600);

    const diags = await session.client.pullDocumentDiagnostics(docUri);
    fileDiagnosticRegistry.updateFromLspUri(session.projectRoot, docUri, diags);
    persistFileDiagnosticsCache(session.projectRoot, docUri, diags);
    return true;
  };

  for (let attempt = 0; attempt < 10; attempt++) {
    if (await syncAndPull()) return;
    await sleep(300);
  }

  // Live LSP doc unavailable — fall back to incremental disk scan so the
  // Problems checkmark still clears after agent workspace_write_file.
  requestProjectDiagnosticsScan(root, { full: false });
}

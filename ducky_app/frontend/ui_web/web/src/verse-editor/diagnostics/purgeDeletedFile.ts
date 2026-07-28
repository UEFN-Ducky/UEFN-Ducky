import { pushLocalAgentEvent } from "../../hooks/useAgentEventBus";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";
import { getVerseLspSession } from "../lsp/verseLspSession";
import { toLspProtocolUri } from "../lsp/uriUtils";

/** File confirmed gone from disk (app delete OR external delete discovered at
 * read time): purge diagnostics for the dead path AND close the phantom LSP
 * document — verse-lsp otherwise keeps analyzing the old in-memory doc, and the
 * Problems panel keeps reporting a ghost file forever. */
export function purgeDeletedFileState(path: string): void {
  fileDiagnosticRegistry.removeFile(path);
  const session = getVerseLspSession();
  if (session) {
    session.client.closeDocument(
      session.client.canonicalUri(toLspProtocolUri(session.projectRoot, path, session.monaco)),
    );
  }
}

/** File confirmed missing while a tab is open (project switch, Explorer delete,
 * etc.): purge Problems/LSP state and emit `file_deleted` so open tabs auto-close.
 * Do not leave a "Close this tab" ghost — that keeps the LSP doc alive and
 * resurrects the stale badge in the top bar. */
export function notifyMissingProjectFile(path: string): void {
  const oldPath = path.replace(/\\/g, "/");
  purgeDeletedFileState(oldPath);
  pushLocalAgentEvent({ type: "file_deleted", old_path: oldPath });
}

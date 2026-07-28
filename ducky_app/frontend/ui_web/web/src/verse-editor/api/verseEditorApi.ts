import { getApi } from "../../hooks/usePanelApi";

import type { VerseLspStatusDto } from "../../types/panel";

import { verseLspLog, verseLspLogError } from "../lsp/verseLspDebug";

/** Stable per-page id: each app window (main / focus) drives its OWN verse-lsp session,
 * like VS Code's one-language-server-per-window. A single shared session made windows
 * steal each other's bridge connection on every editor bind. */
const LSP_CLIENT_ID: string = (() => {
  try {
    return `w-${crypto.randomUUID()}`;
  } catch {
    return `w-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }
})();

export type VerseFileContent = {
  content: string;
  path: string;
};

export async function readVerseFile(
  path: string,
  options?: { quiet?: boolean },
): Promise<VerseFileContent> {
  const api = getApi();

  if (!api) throw new Error("Panel API unavailable");

  try {
    const result = await api.read_project_file(path);
    const canonicalPath = result.path || path;
    verseLspLog("api", "read_project_file ok", { path: canonicalPath, chars: result.content.length });

    return { content: result.content, path: canonicalPath };
  } catch (e) {
    // `quiet` = a speculative/optional read (e.g. digest-doc probes that miss for
    // workspace folders without a digest, like Content/ or vproject/). Those failures are
    // expected — log at info so they don't spam the console as red errors. The call still
    // throws so the caller's own try/catch handles the miss.
    if (options?.quiet) {
      verseLspLog("api", "read_project_file miss (optional)", { path });
    } else {
      verseLspLogError("api", "read_project_file failed", { path, err: e });
    }

    throw e;
  }
}



export async function writeVerseFile(path: string, content: string): Promise<void> {

  const api = getApi();

  if (!api) throw new Error("Panel API unavailable");

  try {

    await api.write_project_file(path, content);

    verseLspLog("api", "write_project_file ok", { path, chars: content.length });

  } catch (e) {

    verseLspLogError("api", "write_project_file failed", { path, err: e });

    throw e;

  }

}

export async function recordExternalFileChange(
  path: string,
  previousContent: string,
  newContent: string,
): Promise<void> {
  const api = getApi();
  if (!api?.record_external_file_change) return;
  try {
    await api.record_external_file_change(path, previousContent, newContent);
    verseLspLog("api", "record_external_file_change ok", { path });
  } catch (e) {
    verseLspLogError("api", "record_external_file_change failed", { path, err: e });
  }
}



export async function getLspStatus(): Promise<VerseLspStatusDto> {

  const api = getApi();

  if (!api) return { available: false, lsp_path: "", source: "", running: false, ws_url: "", project_root: "", error: "no api" };

  try {
    const status = await api.get_verse_lsp_status(LSP_CLIENT_ID);
    verseLspLog("api", "get_verse_lsp_status", status);
    return status;
  } catch (e) {
    // The bridge RPC can reject (e.g. ConnectionResetError while verse-lsp is restarting).
    // Return a not-running status instead of throwing so callers don't leak unhandled rejections.
    verseLspLogError("api", "get_verse_lsp_status failed", e);
    return {
      available: false,
      lsp_path: "",
      source: "",
      running: false,
      ws_url: "",
      project_root: "",
      error: e instanceof Error ? e.message : "verse-lsp status unavailable",
    };
  }

}



export async function stopLsp(): Promise<void> {
  const api = getApi();
  if (!api) return;
  try {
    await api.stop_verse_lsp(LSP_CLIENT_ID);
    verseLspLog("api", "stop_verse_lsp ok");
  } catch (e) {
    verseLspLogError("api", "stop_verse_lsp failed", e);
  }
}

export async function startLsp(projectRoot?: string): Promise<VerseLspStatusDto> {

  const api = getApi();

  if (!api) throw new Error("Panel API unavailable");

  verseLspLog("api", "start_verse_lsp call", { projectRoot });

  try {

    const status = await api.start_verse_lsp(projectRoot, LSP_CLIENT_ID);

    verseLspLog("api", "start_verse_lsp result", status);

    return status;

  } catch (e) {

    verseLspLogError("api", "start_verse_lsp failed", e);

    throw e;

  }

}



export async function isEditorEnabled(): Promise<boolean> {

  const api = getApi();

  if (!api) return false;

  return api.is_verse_editor_enabled();

}


import type { editor } from "monaco-editor";

import { startLsp, getLspStatus } from "../api/verseEditorApi";

import { tryAutoFixMissingUsings } from "../diagnostics/autoFixMissingUsings";
import { applyLspDiagnostics } from "./applyDiagnostics";
import { fileDiagnosticRegistry } from "./fileDiagnosticRegistry";
import { schedulePersistFileDiagnosticsCache } from "../diagnostics/persistFileDiagnosticsCache";
import { registerVerseLspProviders } from "./registerLspProviders";
import type { NavigateToFile } from "./lspNavigation";
import {
  fileUriToRelativePath,
  fileUrisMatch,
  normalizeFileUri,
  VerseLspClient,
  type LspDiagnostic,
} from "./verseLspClient";
import { verseLspLog, verseLspLogError, verseLspWarn } from "./verseLspDebug";
import { isDigestFile } from "../utils/isVerseFile";

export type LspDiagnosticsStats = {
  received: number;
  dropped: number;
  applied: number;
};

const stats: LspDiagnosticsStats = { received: 0, dropped: 0, applied: 0 };

export function getLspDiagnosticsStats(): LspDiagnosticsStats {
  return { ...stats };
}

export function resetLspDiagnosticsStats(): void {
  stats.received = 0;
  stats.dropped = 0;
  stats.applied = 0;
}

if (typeof window !== "undefined") {
  (window as unknown as { __verseLspDiagnostics?: () => LspDiagnosticsStats }).__verseLspDiagnostics =
    getLspDiagnosticsStats;
}

type Session = {
  client: VerseLspClient;
  projectRoot: string;
  workspaceFolderPaths: string[];
  refCount: number;
  providersDisposable: { dispose(): void } | null;
  models: Map<string, editor.ITextModel>;
  monaco: typeof import("monaco-editor");
};

let session: Session | null = null;
let connectPromise: Promise<Session> | null = null;

/** Exact last diagnostics (full ranges) per file for this session — the equivalent of
 * VS Code's markers service. Tab activation restores squiggles from here verbatim. */
const lastDiagnosticsByUri = new Map<string, LspDiagnostic[]>();

export function getLastDiagnosticsForUri(uri: string): LspDiagnostic[] | undefined {
  return lastDiagnosticsByUri.get(normalizeFileUri(uri));
}

type SessionInvalidatedListener = (info: { manual: boolean }) => void;
const invalidationListeners = new Set<SessionInvalidatedListener>();

/** Fired when the LSP session dies unexpectedly (socket drop) or a rebind is requested,
 * so bound editors can re-acquire instead of staying dead until a file switch. */
export function onVerseLspSessionInvalidated(listener: SessionInvalidatedListener): () => void {
  invalidationListeners.add(listener);
  return () => {
    invalidationListeners.delete(listener);
  };
}

function notifySessionInvalidated(manual: boolean): void {
  for (const listener of [...invalidationListeners]) {
    try {
      listener({ manual });
    } catch (e) {
      verseLspLogError("session", "invalidation listener threw", e);
    }
  }
}

/** Explicit user action (recheck button) — tears through backoff and rebinds now. */
export function requestVerseLspSessionRebind(): void {
  verseLspLog("session", "manual rebind requested");
  notifySessionInvalidated(true);
}
const pendingDiagnostics = new Map<string, LspDiagnostic[]>();
const PENDING_DIAGNOSTICS_MAX = 200;

/** Stash push diagnostics for a not-yet-open model, bounded so background scans can't grow it forever. */
function setPendingDiagnostics(key: string, diagnostics: LspDiagnostic[]): void {
  if (pendingDiagnostics.size >= PENDING_DIAGNOSTICS_MAX && !pendingDiagnostics.has(key)) {
    const oldest = pendingDiagnostics.keys().next();
    if (!oldest.done) pendingDiagnostics.delete(oldest.value);
  }
  pendingDiagnostics.set(key, diagnostics);
}

/** Mutable — updated on every editor bind so F12/references use the active tab's openFileAt. */
const navigateToFileRef: { current: NavigateToFile } = {
  current: () => {},
};

export function getNavigateToFile(): NavigateToFile {
  return (req, options) => navigateToFileRef.current(req, options);
}

function modelKey(uri: string): string {
  return normalizeFileUri(uri);
}

function resolveModel(
  monaco: typeof import("monaco-editor"),
  active: Session,
  uri: string,
): editor.ITextModel | undefined {
  const key = modelKey(uri);
  let target = active.models.get(key);
  if (target) return target;

  for (const model of active.models.values()) {
    const docUri = model.uri.toString();
    if (fileUrisMatch(uri, docUri, active.projectRoot)) {
      active.models.set(key, model);
      active.models.set(modelKey(docUri), model);
      return model;
    }
  }

  for (const model of monaco.editor.getModels()) {
    const docUri = model.uri.toString();
    if (fileUrisMatch(uri, docUri, active.projectRoot)) {
      active.models.set(modelKey(docUri), model);
      return model;
    }
  }

  return undefined;
}

function isNonEditorDiagnosticUri(uri: string): boolean {
  const lower = uri.toLowerCase();
  return (
    lower.endsWith(".vproject") ||
    lower.includes("verse.digest") ||
    lower.includes("/verseproject/") ||
    lower.includes("\\verseproject\\")
  );
}

function applyDiagnosticsForUri(
  monaco: typeof import("monaco-editor"),
  active: Session,
  uri: string,
  diagnostics: LspDiagnostic[],
): boolean {
  const target = resolveModel(monaco, active, uri);
  if (!target || target.isDisposed()) {
    setPendingDiagnostics(modelKey(uri), diagnostics);
    stats.dropped += 1;
    if (fileDiagnosticRegistry.isFullScanInProgress() || isNonEditorDiagnosticUri(uri)) {
      return false;
    }
    // A push for a project .verse file with no open tab is expected: the problems panel is
    // already fed upstream via updateFromLspUri; the model only matters for in-editor squiggles.
    // Log at info for known project files, warn only for genuinely unexpected URIs.
    const rel = fileUriToRelativePath(active.projectRoot, uri);
    const isProjectVerseFile = !!rel && rel.toLowerCase().endsWith(".verse");
    const log = isProjectVerseFile ? verseLspLog : verseLspWarn;
    log("session", "diagnostics not applied — file not open in editor", {
      lspUri: uri,
      normalizedKey: modelKey(uri),
      pendingCount: diagnostics.length,
    });
    return false;
  }
  if (tryAutoFixMissingUsings(target, diagnostics)) {
    pendingDiagnostics.delete(modelKey(uri));
    stats.applied += 1;
    return true;
  }
  applyLspDiagnostics(monaco, target, diagnostics);
  pendingDiagnostics.delete(modelKey(uri));
  stats.applied += 1;
  if (fileDiagnosticRegistry.isFullScanInProgress()) {
    return true;
  }
  verseLspLog("session", "diagnostics applied", {
    lspUri: uri,
    modelUri: target.uri.toString(),
    count: diagnostics.length,
  });
  return true;
}

function flushPendingForModel(
  monaco: typeof import("monaco-editor"),
  active: Session,
  uri: string,
  model: editor.ITextModel,
): void {
  const docUri = model.uri.toString();
  for (const [key, diagnostics] of [...pendingDiagnostics.entries()]) {
    if (fileUrisMatch(key, docUri, active.projectRoot) || fileUrisMatch(key, uri, active.projectRoot)) {
      if (!model.isDisposed()) {
        if (!tryAutoFixMissingUsings(model, diagnostics)) {
          applyLspDiagnostics(monaco, model, diagnostics);
        }
      }
      pendingDiagnostics.delete(key);
      stats.applied += 1;
    }
  }
}

/** Surface verse-lsp's own stderr + which binary was launched, so a crash-on-startup is
 * diagnosable instead of showing only "connection refused" in the browser. */
function logLspStartupFailure(
  reason: string,
  status: {
    error?: string;
    source?: string;
    lsp_path?: string;
    stderr_tail?: string[];
    proc_alive?: boolean;
    bridge_up?: boolean;
    last_exit_code?: number | null;
  },
): void {
  const exit = status.last_exit_code;
  verseLspWarn("session", `verse-lsp unavailable — ${reason}`, {
    error: status.error || "",
    source: status.source || "",
    lspPath: status.lsp_path || "",
    procAlive: status.proc_alive ?? null,
    bridgeUp: status.bridge_up ?? null,
    exitCode: exit ?? null,
    // Windows exit codes are clearer in hex (e.g. 0xC0000135 = missing DLL).
    exitCodeHex:
      typeof exit === "number" ? `0x${(exit >>> 0).toString(16).toUpperCase()}` : null,
    stderrTail: status.stderr_tail?.slice(-12) ?? [],
  });
}

async function teardownSession(): Promise<void> {
  if (!session) return;
  verseLspLog("session", "teardown", { refCount: session.refCount });
  const s = session;
  session = null;
  connectPromise = null;
  pendingDiagnostics.clear();
  s.providersDisposable?.dispose();
  // Neutralize the disconnect callback first: an intentional teardown closing the socket
  // must not re-enter teardown or fire the session-invalidated (auto-reconnect) event.
  s.client.onDisconnect(() => {});
  s.client.disconnect();
  fileDiagnosticRegistry.endScan();
}

export async function acquireVerseLspSession(
  monaco: typeof import("monaco-editor"),
  projectRoot: string,
  navigateToFile: NavigateToFile,
): Promise<Session> {
  navigateToFileRef.current = navigateToFile;

  if (session && session.projectRoot === projectRoot) {
    const status = await getLspStatus();
    verseLspLog("session", "reuse check", { status, connected: session.client.isConnected() });
    if (status.running && status.ws_url && session.client.isConnected()) {
      verseLspLog("session", "reuse existing session", { projectRoot });
      if (session.monaco !== monaco) {
        session.monaco = monaco;
      }
      return session;
    }
    await teardownSession();
  }

  if (connectPromise) {
    const pending = await connectPromise;
    if (pending.projectRoot === projectRoot) {
      return pending;
    }
  }

  if (session) {
    await teardownSession();
  }

  connectPromise = (async () => {
    let lastError: Error | null = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      verseLspLog("session", "start_lsp attempt", { attempt, projectRoot });
      const status = await startLsp(projectRoot);
      verseLspLog("session", "start_lsp result", status);
      if (!status.available) {
        throw new Error(status.error || "verse-lsp not found — syntax check unavailable");
      }
      if (!status.running || !status.ws_url) {
        lastError = new Error(status.error || "verse-lsp bridge failed to start");
        logLspStartupFailure("bridge not running (verse-lsp likely crashed on startup)", status);
        continue;
      }

      const resolvedRoot = status.project_root || projectRoot;
      const client = new VerseLspClient();
      client.onDisconnect(() => {
        // Unexpected socket drop: tear down, then tell bound editors to reconnect
        // (VS Code-style auto-restart) instead of staying dead until a file switch.
        void teardownSession().then(() => notifySessionInvalidated(false));
      });
      try {
        await client.connect(status.ws_url, {
          projectRoot: resolvedRoot,
          workspaceFolders: status.workspace_folders,
          watchFiles: status.watch_files,
          monaco,
        });
        lastError = null;

        const models = new Map<string, editor.ITextModel>();
        const workspaceFolderPaths = (status.workspace_folders ?? []).map((f) => f.path);

        const next: Session = {
          client,
          projectRoot: resolvedRoot,
          workspaceFolderPaths,
          refCount: 1,
          providersDisposable: null,
          models,
          monaco,
        };
        session = next;

        client.onDiagnostics((uri, diagnostics) => {
          stats.received += 1;
          const active = session;
          if (!active) return;
          // Digest files (Verse/Fortnite/Assets/UnrealEngine.digest.verse) are Epic's
          // generated, read-only API surface — everything depends on them and they can't be
          // edited, so any squiggles the LSP emits on them are noise. Drop them entirely:
          // no problems-panel entry, no editor markers, no cache persistence.
          if (isDigestFile(uri)) {
            stats.dropped += 1;
            return;
          }
          lastDiagnosticsByUri.set(normalizeFileUri(uri), diagnostics);
          fileDiagnosticRegistry.updateFromLspUri(active.projectRoot, uri, diagnostics);
          applyDiagnosticsForUri(monaco, active, uri, diagnostics);
          schedulePersistFileDiagnosticsCache(active.projectRoot, uri, diagnostics);
        });

        next.providersDisposable = registerVerseLspProviders(
          monaco,
          client,
          resolvedRoot,
          "",
          workspaceFolderPaths,
        );

        verseLspLog("session", "session ready", { projectRoot: resolvedRoot, ws_url: status.ws_url });
        return next;
      } catch (e) {
        session = null;
        lastError = e instanceof Error ? e : new Error("LSP connect failed");
        verseLspLogError("session", "connect attempt failed", lastError);
        // Re-read status after a short settle so the exit-watcher has recorded the crash
        // (exit code + stderr) that closed the socket.
        try {
          await new Promise((r) => setTimeout(r, 250));
          logLspStartupFailure("websocket connect failed", await getLspStatus());
        } catch {
          logLspStartupFailure("websocket connect failed", status);
        }
      }
    }
    throw lastError ?? new Error("verse-lsp bridge failed to start — syntax check unavailable");
  })();

  try {
    return await connectPromise;
  } finally {
    connectPromise = null;
  }
}

export function registerLspModel(uri: string, model: editor.ITextModel): void {
  if (!session) {
    verseLspWarn("session", "registerLspModel — no session", { uri });
    return;
  }
  const modelUri = model.uri.toString();
  session.models.set(modelKey(uri), model);
  session.models.set(modelKey(modelUri), model);
  verseLspLog("session", "registerLspModel", {
    uri,
    modelUri,
    key: modelKey(modelUri),
  });
  flushPendingForModel(session.monaco, session, uri, model);
}

export function unregisterLspModel(uri: string): void {
  if (!session) return;
  const model = session.models.get(modelKey(uri));
  session.models.delete(modelKey(uri));
  if (model) {
    session.models.delete(modelKey(model.uri.toString()));
  }
}

/** Keep verse-lsp aware of project files opened via go-to-definition (before a tab mounts). */
export function ensureLspDocumentOpen(uri: string, text: string): void {
  if (!session) return;
  const wireUri = session.client.canonicalUri(uri);
  if (session.client.isDocumentOpen(wireUri)) return;
  session.client.openDocument(uri, text);
  verseLspLog("session", "ensureLspDocumentOpen", { uri: wireUri, chars: text.length });
}

export function releaseVerseLspSession(): void {
  // Project switch: stale markers from the old project must not restore into the new one.
  lastDiagnosticsByUri.clear();
  if (!session) return;
  verseLspLog("session", "release — teardown project session");
  void teardownSession();
}

export function getVerseLspSession(): Session | null {
  return session;
}

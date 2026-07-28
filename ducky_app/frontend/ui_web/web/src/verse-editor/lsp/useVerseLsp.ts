import { useEffect, useRef, useState } from "react";

import type { editor } from "monaco-editor";

import { getLspStatus } from "../api/verseEditorApi";
import {
  cancelOpenFileDiagnosticRefresh,
  refreshOpenFileDiagnosticsNow,
  scheduleOpenFileDiagnosticRefresh,
} from "../diagnostics/refreshOpenFileDiagnostics";
import { isVerseDiagnosticsAutoCheckEnabled } from "../diagnostics/verseDiagnosticsSettings";
import { useVerseEditor } from "../VerseEditorProvider";

import {
  applyLspDiagnostics,
  applyStoredDiagnostics,
  readVerseLspMarkersAsDiagnostics,
} from "./applyDiagnostics";
import { fileDiagnosticRegistry } from "./fileDiagnosticRegistry";
import { persistFileDiagnosticsCache } from "../diagnostics/persistFileDiagnosticsCache";
import { isDigestFile } from "../utils/isVerseFile";
import { type NavigateToFile, revealRequestName } from "./lspNavigation";
import {
  acquireVerseLspSession,
  getLastDiagnosticsForUri,
  getVerseLspSession,
  onVerseLspSessionInvalidated,
  registerLspModel,
  unregisterLspModel,
} from "./verseLspSession";
import { verseLspLog, verseLspLogError } from "./verseLspDebug";

/**
 * Binds the active Monaco editor to a shared verse-lsp session (one bridge per project).
 */
export function useVerseLsp(
  monaco: typeof import("monaco-editor") | null,
  ed: editor.IStandaloneCodeEditor | null,
  relativePath: string,
  projectRoot: string,
  _playbackLocked: boolean,
): { lspError: string; lspReady: boolean } {
  const [lspError, setLspError] = useState("");
  const [lspReady, setLspReady] = useState(false);
  const [rebindTick, setRebindTick] = useState(0);
  const changeDisposableRef = useRef<{ dispose(): void } | null>(null);
  const docUriRef = useRef("");
  const bindGenerationRef = useRef(0);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A bind (connect + didOpen) is currently running. Rebind requests that land while this
  // is true must be COALESCED, never applied immediately — applying one bumps the bind
  // generation and cancels the in-flight attempt, which is exactly the attempt that
  // reopens the doc after a socket drop. Cancelling it in a loop = the doc never reopens
  // (the endless "doc-not-open → rebind → cancel bind → doc-not-open" spam).
  const bindInFlightRef = useRef(false);
  const pendingRebindRef = useRef(false);
  const { requestReveal, openFileAt, isAgentActiveOnPath } = useVerseEditor();

  // These context callbacks change identity whenever the provider re-renders (its
  // `queue` memo depends on ChatView props that aren't all stable). They are only
  // ever invoked at event time — F12 navigation and onDidChangeModelContent — never
  // at bind time, so hold them in refs and keep the bind effect OUT of their
  // dependency list. Depending on them directly made the effect unbind→rebind on
  // every render: a self-sustaining loop (each bind toggles lspReady → re-render →
  // new callback identities → rebind) that pegged CPU and hammered verse-lsp.
  const requestRevealRef = useRef(requestReveal);
  const openFileAtRef = useRef(openFileAt);
  const isAgentActiveOnPathRef = useRef(isAgentActiveOnPath);
  requestRevealRef.current = requestReveal;
  openFileAtRef.current = openFileAt;
  isAgentActiveOnPathRef.current = isAgentActiveOnPath;

  // Session died (socket drop) or a manual rebind was requested: re-run the bind effect.
  // A manual request (recheck button) resets the backoff so it reconnects immediately.
  useEffect(() => {
    return onVerseLspSessionInvalidated(({ manual }) => {
      if (manual) reconnectAttemptsRef.current = 0;
      if (bindInFlightRef.current) {
        // Don't restart an in-flight bind — coalesce and let it finish (it's the attempt
        // that reopens the doc). Restarting here is the root of the doc-not-open loop
        // after a 1006 socket drop.
        pendingRebindRef.current = true;
        verseLspLog("hook", "rebind coalesced — bind in flight", { manual });
        return;
      }
      verseLspLog("hook", "session invalidated — rebinding", { manual });
      setRebindTick((t) => t + 1);
    });
  }, []);

  useEffect(() => {
    if (!monaco || !ed || !projectRoot) {
      if (!projectRoot) verseLspLog("hook", "waiting for projectRoot");
      else if (!monaco) verseLspLog("hook", "waiting for monaco");
      else if (!ed) verseLspLog("hook", "waiting for editor");
      setLspReady(false);
      return;
    }

    const model = ed.getModel();
    if (!model) {
      verseLspLog("hook", "no editor model");
      return;
    }

    const docUri = model.uri.toString();
    docUriRef.current = docUri;
    verseLspLog("hook", "bind start", { relativePath, projectRoot, docUri });

    let cancelled = false;
    let documentSynced = false;
    const bindGeneration = ++bindGenerationRef.current;

    void (async () => {
      bindInFlightRef.current = true;
      let bindSucceeded = false;
      try {
        const navigateToFile: NavigateToFile = (req, options) => {
          verseLspLog("hook", "navigate request", req);
          openFileAtRef.current(req.path, revealRequestName(req), options);
          requestRevealRef.current(req.path, req.line, req.column);
        };

        const sess = await acquireVerseLspSession(monaco, projectRoot, navigateToFile);
        if (cancelled || bindGeneration !== bindGenerationRef.current) return;
        // The Host can dispose+recreate the model while acquire was in flight (fast tab
        // churn) — its effect cleanup ordering isn't guaranteed relative to ours.
        if (model.isDisposed()) {
          verseLspLog("hook", "bind aborted — model disposed during acquire", { docUri });
          return;
        }

        registerLspModel(docUri, model);
        // VS Code lifecycle: didOpen only if the server doesn't already have this doc
        // (openDocument no-ops content-identical reopens); NO didClose on tab switch,
        // so re-activating a tab costs zero LSP traffic.
        sess.client.openDocument(docUri, model.getValue());
        sess.client.flushChanges(docUri);
        documentSynced = true;
        verseLspLog("hook", "document synced to LSP", { docUri });

        if (cancelled || bindGeneration !== bindGenerationRef.current) {
          unregisterLspModel(docUri);
          documentSynced = false;
          return;
        }

        changeDisposableRef.current?.dispose();
        changeDisposableRef.current = ed.onDidChangeModelContent(() => {
          if (isAgentActiveOnPathRef.current(relativePath)) return;
          const currentModel = ed.getModel();
          if (!currentModel) return;
          const uri = currentModel.uri.toString();
          sess.client.changeDocument(currentModel.getValue(), uri);
          scheduleOpenFileDiagnosticRefresh(projectRoot, relativePath);
        });

        // Restore squiggles with NO LSP request — markers persist across tab switches
        // exactly like VS Code's marker service. Prefer the session's exact last
        // diagnostics (full ranges, pixel-identical squiggles). Do NOT paint Problems-
        // cache zombies when auto-check will pull live verse-lsp next — that left the
        // file tree red while the open buffer was actually clean.
        // Digest files are read-only generated API surface — diagnostics on them are
        // suppressed at the session router, so never restore stale cached squiggles for
        // them either (clear any that a pre-suppression session may have persisted).
        const autoCheck = isVerseDiagnosticsAutoCheckEnabled();
        if (isDigestFile(relativePath)) {
          applyLspDiagnostics(monaco, model, []);
        } else {
          const exact = getLastDiagnosticsForUri(docUri);
          if (exact) {
            applyLspDiagnostics(monaco, model, exact);
          } else if (!autoCheck) {
            const stored = fileDiagnosticRegistry.getItems(relativePath);
            if (stored.length) {
              applyStoredDiagnostics(monaco, model, stored);
            }
          }
        }

        setLspError("");
        setLspReady(true);
        reconnectAttemptsRef.current = 0;
        bindSucceeded = true;
        verseLspLog("hook", "bind complete — LSP ready");
        // If Monaco already has squiggles but Problems was falsely cleared, sync them
        // into the registry BEFORE the backup pull (empty pulls must not hide these).
        if (!isDigestFile(relativePath)) {
          const markerDiags = readVerseLspMarkersAsDiagnostics(monaco, model);
          if (markerDiags.length > 0) {
            fileDiagnosticRegistry.updateFromLspUri(projectRoot, docUri, markerDiags);
            persistFileDiagnosticsCache(projectRoot, docUri, markerDiags);
          }
        }
        // Settings promise "re-check on open": pull live diagnostics (empty pull is ignored).
        if (autoCheck && !isDigestFile(relativePath)) {
          refreshOpenFileDiagnosticsNow(projectRoot, relativePath);
        }
      } catch (e) {
        if (!cancelled && bindGeneration === bindGenerationRef.current) {
          const msg = e instanceof Error ? e.message : "LSP unavailable";
          verseLspLogError("hook", "bind failed", e);
          setLspError(
            msg.includes("exited") || msg.includes("disconnect")
              ? `${msg} — close and reopen the file`
              : msg,
          );
          setLspReady(false);
          // Auto-retry with exponential backoff (1s → 2s → 4s → 8s → 15s), capped so a
          // dead backend doesn't loop forever. The recheck button resets the counter.
          const attempt = reconnectAttemptsRef.current;
          if (attempt < 5) {
            reconnectAttemptsRef.current = attempt + 1;
            const delayMs = Math.min(15_000, 1_000 * 2 ** attempt);
            verseLspLog("hook", "scheduling LSP reconnect", { attempt: attempt + 1, delayMs });
            reconnectTimerRef.current = setTimeout(() => setRebindTick((t) => t + 1), delayMs);
          }
        }
      } finally {
        // Only clear the flag if we still own this generation — a newer bind may have
        // superseded us (then it owns the flag). A rebind that arrived while we were
        // connecting was coalesced; honor it now that we've settled, but ONLY if this
        // attempt failed — on success the doc is already open, so a redo is pure churn.
        if (bindGeneration === bindGenerationRef.current) {
          bindInFlightRef.current = false;
          if (pendingRebindRef.current && !cancelled) {
            pendingRebindRef.current = false;
            if (!bindSucceeded) {
              verseLspLog("hook", "honoring coalesced rebind after failed bind");
              setRebindTick((t) => t + 1);
            }
          }
        }
      }
    })();

    return () => {
      cancelled = true;
      verseLspLog("hook", "unbind", { relativePath, docUri: docUriRef.current });
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      changeDisposableRef.current?.dispose();
      changeDisposableRef.current = null;
      cancelOpenFileDiagnosticRefresh(projectRoot, relativePath);

      const uri = docUriRef.current;
      const s = getVerseLspSession();
      if (documentSynced && s && uri) {
        // Flush pending edits but keep the document OPEN server-side and keep its
        // markers — a tab switch must not trigger didClose/didOpen re-analysis or
        // wipe squiggles (they persist like VS Code's marker service). The doc closes
        // with the session (project switch / teardown).
        s.client.flushChanges(uri);
        unregisterLspModel(uri);
      }
      setLspReady(false);
    };
    // Deliberately NOT depending on requestReveal/openFileAt/isAgentActiveOnPath —
    // they are read through refs above, so a parent re-render can't force a rebind.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monaco, ed, relativePath, projectRoot, rebindTick]);

  useEffect(() => {
    if (!projectRoot) return;
    void getLspStatus().then((status) => {
      if (!status.available && !lspReady) {
        setLspError(status.error || "verse-lsp not found — syntax check unavailable");
      }
    });
  }, [projectRoot, lspReady]);

  return { lspError, lspReady };
}

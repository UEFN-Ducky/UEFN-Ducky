import { persistFileDiagnosticsCache } from "./persistFileDiagnosticsCache";
import { normProjectRoot } from "./requestProjectScan";
import { isVerseDiagnosticsAutoCheckEnabled } from "./verseDiagnosticsSettings";
import { readVerseLspMarkersAsDiagnostics } from "../lsp/applyDiagnostics";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";
import type { LspDiagnostic } from "../lsp/verseLspClient";
import {
  getLastDiagnosticsForUri,
  getVerseLspSession,
  requestVerseLspSessionRebind,
} from "../lsp/verseLspSession";
import { resolveOpenDocUri } from "../lsp/resolveOpenDocUri";
import { fileUrisMatch } from "../lsp/uriUtils";
import { registryKey } from "../utils/isVerseFile";
import { verseLspLog, verseLspWarn } from "../lsp/verseLspDebug";

const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>();
const lastPullAt = new Map<string, number>();
const MIN_PULL_INTERVAL_MS = 800;

function fileKey(projectRoot: string, relativePath: string): string {
  return `${normProjectRoot(projectRoot)}:${registryKey(relativePath)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Re-check diagnostics for one open editor file via live verse-lsp (never a project scan). */
export async function refreshOpenFileDiagnostics(
  projectRoot: string,
  relativePath: string,
  options?: { waitMs?: number; flush?: boolean; force?: boolean },
): Promise<boolean> {
  // `force` = explicit user action (e.g. the recheck-current-file button): always run,
  // ignoring the auto-check toggle and the throttle window.
  const force = options?.force ?? false;

  if (!force && !isVerseDiagnosticsAutoCheckEnabled()) {
    verseLspWarn("diagnostics", "refresh skipped", { reason: "auto-check-disabled", relativePath });
    return false;
  }

  const pullKey = fileKey(projectRoot, relativePath);
  const now = Date.now();
  const last = lastPullAt.get(pullKey) ?? 0;
  if (!force && now - last < MIN_PULL_INTERVAL_MS && (options?.waitMs ?? 400) <= 600) {
    // NEVER drop a throttled check — reschedule it for when the window opens, so the
    // user's last edit is always checked. Dropping here left stale squiggles whenever
    // the debounced check landed inside the throttle window.
    const retryInMs = Math.max(150, MIN_PULL_INTERVAL_MS - (now - last) + 50);
    verseLspLog("diagnostics", "refresh throttled — rescheduled", { relativePath, retryInMs });
    scheduleOpenFileDiagnosticRefresh(projectRoot, relativePath, retryInMs);
    return false;
  }

  let resolved = resolveOpenDocUri(projectRoot, relativePath);
  if (!resolved && force) {
    const active = getVerseLspSession();
    if (!active || !active.client.isConnected()) {
      // Only a genuinely dead session (socket drop) warrants a rebind + wait-for-reconnect.
      verseLspWarn("diagnostics", "doc not open — session down, requesting LSP reconnect", {
        relativePath,
      });
      requestVerseLspSessionRebind();
      for (let i = 0; i < 10 && !resolved; i++) {
        await sleep(500);
        resolved = resolveOpenDocUri(projectRoot, relativePath);
      }
    }
    // Session ALIVE but the doc isn't open — a stale tab for a file that no longer exists,
    // or a tab from the project we just switched away from. Do NOT rebind: nuking the live
    // session in a loop for a ghost file (each rebind resets the backoff) is the
    // "recheck / search / swap-projects lags forever and won't stop" churn. Just skip.
  }
  if (!resolved) {
    verseLspWarn("diagnostics", "refresh skipped", { reason: "doc-not-open", relativePath });
    return false;
  }

  const session = getVerseLspSession();
  if (!session?.client.isConnected()) return false;

  // PUSH-first (VS Code never pulls): if the server already published diagnostics for
  // this file a moment ago, this backup pull is pure duplicate load on verse-lsp —
  // pulls piling up during heavy analysis were timing out at 45s and wedging the UX.
  if (!force && Date.now() - fileDiagnosticRegistry.getLiveUpdateAt(relativePath) < 1500) {
    verseLspLog("diagnostics", "push already fresh — pull skipped", { relativePath });
    return true;
  }

  const { docUri } = resolved;
  if (options?.flush !== false) {
    session.client.flushChanges(docUri);
  }

  const waitMs = options?.waitMs ?? 500;
  if (waitMs > 0) await sleep(waitMs);
  if (!session.client.isConnected()) return false;

  try {
    // Spinner only for explicit user checks — background typing checks kept the
    // header spinning nonstop whenever verse-lsp answered slowly.
    if (force) fileDiagnosticRegistry.beginFileCheck(relativePath);
    lastPullAt.set(pullKey, Date.now());
    const pulled = await session.client.pullDocumentDiagnostics(docUri);
    // verse-lsp often returns a FALSE empty set on pull (same quirk as batch scans).
    // Prefer open-buffer Monaco squiggles over a false empty. If the buffer is also
    // clean, treat that as a real clear so the file tree / Problems match the editor
    // (stale lastPush used to keep the left side red after a real fix).
    let diags: LspDiagnostic[] = pulled;
    if (diags.length === 0) {
      let markers: LspDiagnostic[] = [];
      for (const model of session.models.values()) {
        if (model.isDisposed()) continue;
        if (fileUrisMatch(model.uri.toString(), docUri, session.projectRoot)) {
          markers = readVerseLspMarkersAsDiagnostics(session.monaco, model);
          break;
        }
      }
      if (markers.length > 0) {
        diags = markers;
        verseLspWarn("diagnostics", "empty pull ignored — using Monaco squiggles", {
          relativePath,
          errors: diags.length,
        });
      } else {
        const lastPush = getLastDiagnosticsForUri(docUri) ?? [];
        if (lastPush.length > 0) {
          verseLspWarn("diagnostics", "empty pull + clean buffer — clearing stale lastPush", {
            relativePath,
            staleErrors: lastPush.length,
          });
        } else {
          verseLspLog("diagnostics", "empty pull + clean buffer — clearing Problems/cache", {
            relativePath,
          });
        }
        diags = [];
      }
    }
    fileDiagnosticRegistry.updateFromLspUri(session.projectRoot, docUri, diags);
    persistFileDiagnosticsCache(session.projectRoot, docUri, diags);
    verseLspLog("diagnostics", "refresh ok", {
      relativePath,
      errors: diags.length,
    });
    return true;
  } catch (e) {
    verseLspWarn("diagnostics", "refresh skipped", {
      reason: "pull-error",
      relativePath,
      error: e instanceof Error ? e.message : String(e),
    });
    return false;
  } finally {
    if (force) fileDiagnosticRegistry.endFileCheck(relativePath);
  }
}

export function cancelAllOpenFileDiagnosticRefreshes(): void {
  for (const timer of pendingTimers.values()) {
    clearTimeout(timer);
  }
  pendingTimers.clear();
}

export function cancelOpenFileDiagnosticRefresh(projectRoot: string, relativePath: string): void {
  const key = fileKey(projectRoot, relativePath);
  const timer = pendingTimers.get(key);
  if (timer) {
    clearTimeout(timer);
    pendingTimers.delete(key);
  }
}

/** Debounced single-file check while typing (0.9s after the LAST keystroke). This is only a
 * backup pull — push diagnostics from didChange are the live path. Fires ONLY from content
 * changes (typing/paste), never from selection or cursor movement. */
export function scheduleOpenFileDiagnosticRefresh(
  projectRoot: string,
  relativePath: string,
  delayMs = 900,
): void {
  if (!isVerseDiagnosticsAutoCheckEnabled()) return;
  const key = fileKey(projectRoot, relativePath);
  const existing = pendingTimers.get(key);
  if (existing) clearTimeout(existing);
  pendingTimers.set(
    key,
    setTimeout(() => {
      pendingTimers.delete(key);
      void refreshOpenFileDiagnostics(projectRoot, relativePath);
    }, delayMs),
  );
}

/** Immediate single-file check — file open or save (not every focus). `force` bypasses the
 * auto-check toggle and throttle for explicit user actions (recheck-current-file button). */
export function refreshOpenFileDiagnosticsNow(
  projectRoot: string,
  relativePath: string,
  force = false,
): void {
  cancelOpenFileDiagnosticRefresh(projectRoot, relativePath);
  void refreshOpenFileDiagnostics(projectRoot, relativePath, { waitMs: 700, force });
}

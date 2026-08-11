import { registryKey, registryLookupKeys } from "../utils/isVerseFile";
import { fileUriToRelativePath } from "./uriUtils";
import { verseLspWarn } from "./verseLspDebug";
import type { LspDiagnostic } from "./verseLspClient";
import {
  countDiagnosticSeverities,
  lspDiagnosticsToItems,
  type FileDiagnosticItem,
} from "./diagnosticCounts";

export type { FileDiagnosticItem };

export type FileDiagnosticSummary = { errors: number; warnings: number };

export type FileDiagnosticEntry = FileDiagnosticSummary & { items: FileDiagnosticItem[] };

export type FileProblemsGroup = {
  path: string;
  errors: number;
  warnings: number;
  items: FileDiagnosticItem[];
};

export type DiagnosticsScanPhase = "idle" | "scanning" | "ready";

export type ScanFileStatus = "pending" | "active" | "done";

export type ScanProgressPhase = "opening" | "analyzing" | "checking" | "done" | "error" | "idle";

export type ScanProgressState = {
  total: number;
  done: number;
  phase: ScanProgressPhase;
  currentPath: string;
  message: string;
  paths: readonly string[];
  fileStatuses: ReadonlyMap<string, ScanFileStatus>;
};

type ScanFilePayload = {
  path: string;
  errors: number;
  warnings: number;
  items?: Array<{ line: number; column: number; message: string; severity: string }>;
};

const entries = new Map<string, FileDiagnosticEntry>();
/** When each file last received live LSP diagnostics (push or pull) — lets the backup
 * pull skip files the server already reported moments ago. */
const liveUpdateAt = new Map<string, number>();
const listeners = new Set<() => void>();
let scanPhase: DiagnosticsScanPhase = "idle";
let activeScanId = 0;
/** Backend scan generation from progress events — lets us drop trailing events of a
 * superseded scan (its "done" used to wipe the live scan's progress UI). */
let serverScanId = 0;
let fullScanActive = false;
let scanProgress: ScanProgressState | null = null;
let scanLastActivityAt = 0;
const checkingFiles = new Set<string>();

function notify(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch {
      // ignore subscriber errors
    }
  }
}

/** Count files in a given scan status — used so parallel scans report accurate done/active totals. */
function countStatus(statuses: ReadonlyMap<string, ScanFileStatus>, status: ScanFileStatus): number {
  let n = 0;
  for (const v of statuses.values()) if (v === status) n += 1;
  return n;
}

function applyFileResult(file: ScanFilePayload): void {
  // Add-only for scan/cache. Batch scans and the disk cache are best-effort and routinely
  // under-report: verse-lsp emits parse errors only on didChange (never on the scan's
  // didOpen) and its pushes are non-deterministic, so a "0 errors" from these sources
  // means "didn't find any", NOT "this file is clean". Clearing on it wiped real
  // squiggles the live editor had just shown. Only an authoritative live push
  // (updateFromLspUri) may clear a file.
  if (file.errors === 0 && file.warnings === 0) return;
  const key = registryKey(file.path);
  // Live empty push cleared this file — do not resurrect stale scan/cache errors.
  // That left the file tree red while the open buffer was already clean.
  if (liveUpdateAt.has(key) && !entries.has(key)) return;
  const items = normalizeScanItems(file.items);
  setEntry(key, { errors: file.errors, warnings: file.warnings, items });
}

/** verse-lsp often emits the same Unknown-identifier twice on one line (type + ctor).
 * Problems shows one row per line+message; keep the left-most column. */
function dedupeItems(items: FileDiagnosticItem[]): FileDiagnosticItem[] {
  const best = new Map<string, FileDiagnosticItem>();
  for (const item of items) {
    const key = `${item.severity}\0${item.line}\0${item.message}`;
    const prev = best.get(key);
    if (!prev || item.column < prev.column) best.set(key, item);
  }
  return [...best.values()].sort((a, b) => a.line - b.line || a.column - b.column);
}

function setEntry(key: string, entry: FileDiagnosticEntry): void {
  const canonical = registryKey(key);
  for (const alias of registryLookupKeys(key)) {
    if (alias !== canonical) entries.delete(alias);
  }
  const items = dedupeItems(entry.items);
  let errors = 0;
  let warnings = 0;
  for (const item of items) {
    if (item.severity === "warning") warnings += 1;
    else errors += 1;
  }
  if (errors === 0 && warnings === 0) {
    for (const alias of registryLookupKeys(key)) entries.delete(alias);
  } else {
    entries.set(canonical, { errors, warnings, items });
    // Cap growth across long sessions (closed files keep entries until project switch).
    if (entries.size > 2000) {
      const drop = entries.size - 1800;
      let i = 0;
      for (const k of entries.keys()) {
        if (i++ >= drop) break;
        entries.delete(k);
        liveUpdateAt.delete(k);
      }
    }
  }
  notify();
}

function endScanInternal(): void {
  scanPhase = "ready";
  fullScanActive = false;
  scanProgress = null;
  notify();
}

function normalizeScanItems(
  items: Array<{ line: number; column: number; message: string; severity: string }> | undefined,
): FileDiagnosticItem[] {
  if (!Array.isArray(items)) return [];
  const out: FileDiagnosticItem[] = [];
  for (const item of items) {
    const severity = item.severity === "warning" ? "warning" : "error";
    out.push({
      line: item.line,
      column: item.column,
      message: String(item.message || ""),
      severity,
    });
  }
  return out;
}

export const fileDiagnosticRegistry = {
  getScanPhase(): DiagnosticsScanPhase {
    return scanPhase;
  },

  getScanProgress(): ScanProgressState | null {
    return scanProgress;
  },

  isFullScanInProgress(): boolean {
    return scanPhase === "scanning" && fullScanActive;
  },

  isScanInProgress(): boolean {
    return scanPhase === "scanning";
  },

  isFileCheckInProgress(): boolean {
    return checkingFiles.size > 0;
  },

  beginFileCheck(path: string): void {
    checkingFiles.add(registryKey(path));
    notify();
  },

  endFileCheck(path: string): void {
    checkingFiles.delete(registryKey(path));
    notify();
  },

  tickScanWatchdog(): void {
    if (scanPhase !== "scanning") return;
    // Inactivity-based: a healthy scan emits progress every few seconds, however long
    // the whole project takes. The old absolute 120s cutoff killed the UI mid-scan
    // while the backend kept working.
    const idle = Date.now() - scanLastActivityAt;
    if (idle > 120_000) {
      endScanInternal();
      return;
    }
    // Fast-fail: if no files were ever enumerated (the bridge never answered — e.g. verse-lsp
    // down / connection refused), stop spinning after 15s instead of hanging the full 120s.
    // beginScan seeds phase "opening" with an empty paths list; a live scan replaces both.
    if (idle > 15_000 && scanProgress && scanProgress.paths.length === 0) {
      endScanInternal();
    }
  },

  getActiveScanId(): number {
    return activeScanId;
  },

  beginScan(full = false): number {
    activeScanId += 1;
    scanLastActivityAt = Date.now();
    if (scanPhase === "scanning" && scanProgress) {
      // A scan is already streaming (another window or a queued follow-up) — join it
      // instead of wiping the live file list back to "Starting…".
      fullScanActive = fullScanActive || full;
      notify();
      return activeScanId;
    }
    scanPhase = "scanning";
    fullScanActive = full;
    scanProgress = {
      total: 0,
      done: 0,
      phase: "opening",
      currentPath: "",
      message: full ? "Starting full project scan…" : "Starting scan…",
      paths: [],
      fileStatuses: new Map(),
    };
    notify();
    return activeScanId;
  },

  endScan(scanId?: number): void {
    if (scanId !== undefined && scanId !== activeScanId) return;
    endScanInternal();
  },

  clear(): void {
    entries.clear();
    liveUpdateAt.clear();
    notify();
  },

  hydrate(
    files: Array<{
      path: string;
      errors: number;
      warnings: number;
      items?: Array<{ line: number; column: number; message: string; severity: string }>;
    }>,
  ): void {
    for (const file of files) {
      applyFileResult(file);
    }
    notify();
  },

  /** Live LSP update for a single open file — never triggers a project scan. An empty array
   * clears the file (it's the authoritative per-file result for the doc you're editing). */
  updateFromLspUri(projectRoot: string, lspUri: string, diagnostics: LspDiagnostic[]): void {
    const rel = fileUriToRelativePath(projectRoot, lspUri);
    if (!rel || !rel.toLowerCase().endsWith(".verse")) {
      const lower = lspUri.toLowerCase();
      // Digest/VerseProject pushes are expected (Epic's generated API surface lives
      // outside the project root) — only warn about genuinely unexpected URIs.
      const isDigest = lower.includes("verseproject") || lower.includes(".digest");
      if (lower.includes(".verse") && !isDigest) {
        verseLspWarn("registry", "diagnostics rejected — uri not under project root", {
          lspUri,
          projectRoot,
        });
      }
      return;
    }
    if (!registryKey(rel).startsWith("content/")) {
      verseLspWarn("registry", "diagnostics rejected — outside Content/", { rel });
      return;
    }
    liveUpdateAt.set(registryKey(rel), Date.now());
    const counts = countDiagnosticSeverities(diagnostics);
    const items = lspDiagnosticsToItems(diagnostics);
    setEntry(registryKey(rel), { ...counts, items });
  },

  /** Timestamp of the last live LSP diagnostics for a file (push or pull), or 0. */
  getLiveUpdateAt(path: string): number {
    return liveUpdateAt.get(registryKey(path)) ?? 0;
  },

  applyScanProgress(payload: {
    phase?: string;
    scan_id?: number;
    index?: number;
    total?: number;
    path?: string;
    paths?: string[];
    message?: string;
    file?: ScanFilePayload;
  }): void {
    const phase = String(payload.phase || "");
    const eventScanId = typeof payload.scan_id === "number" ? payload.scan_id : 0;
    if (eventScanId && serverScanId && eventScanId < serverScanId) {
      // Superseded scan generation: its diagnostic RESULTS are still valid, but its
      // lifecycle events must not touch the live scan's progress.
      if (phase === "file" && payload.file) applyFileResult(payload.file);
      return;
    }
    if (eventScanId) serverScanId = eventScanId;
    scanLastActivityAt = Date.now();
    if (phase === "started") {
      const paths = Array.isArray(payload.paths) ? payload.paths.map((p) => registryKey(p)) : [];
      const statuses = new Map<string, ScanFileStatus>();
      for (const p of paths) statuses.set(p, "pending");
      scanPhase = "scanning";
      scanProgress = {
        total: payload.total ?? paths.length,
        done: 0,
        phase: "opening",
        currentPath: "",
        message: paths.length ? `Found ${paths.length} Verse files` : "No Verse files in project",
        paths,
        fileStatuses: statuses,
      };
      notify();
      return;
    }

    if (phase === "opening" || phase === "checking") {
      if (!scanProgress) return;
      const path = registryKey(payload.path || "");
      // Additive: many files are checked in parallel, so keep every in-flight file marked
      // "active" instead of demoting to a single current file.
      const statuses = new Map(scanProgress.fileStatuses);
      if (path) statuses.set(path, "active");
      const total = payload.total ?? scanProgress.total;
      const doneCount = countStatus(statuses, "done");
      const activeCount = countStatus(statuses, "active");
      scanProgress = {
        ...scanProgress,
        done: doneCount,
        phase: phase as ScanProgressPhase,
        currentPath: path,
        message:
          activeCount > 1
            ? `Checking ${activeCount} files… (${doneCount}/${total})`
            : `Checking ${Math.min(doneCount + 1, total)}/${total}${path ? ` — ${path.split("/").pop()}` : ""}`,
        fileStatuses: statuses,
      };
      notify();
      return;
    }

    if (phase === "analyzing") {
      if (!scanProgress) return;
      const index = payload.index ?? scanProgress.done;
      const total = payload.total ?? scanProgress.total;
      scanProgress = {
        ...scanProgress,
        done: Math.max(scanProgress.done, Math.max(0, index - 1)),
        total,
        phase: "analyzing",
        currentPath: "",
        message: payload.message || `Analyzing ${index}/${total}…`,
      };
      notify();
      return;
    }

    if (phase === "file" && payload.file) {
      applyFileResult(payload.file);
      if (scanProgress) {
        const key = registryKey(payload.file.path);
        const statuses = new Map(scanProgress.fileStatuses);
        statuses.set(key, "done");
        const doneCount = countStatus(statuses, "done");
        const activeCount = countStatus(statuses, "active");
        scanProgress = {
          ...scanProgress,
          done: doneCount,
          phase: "checking",
          currentPath: key,
          message:
            activeCount > 0
              ? `Checking ${activeCount} files… (${doneCount}/${scanProgress.total})`
              : `${doneCount}/${scanProgress.total} files`,
          fileStatuses: statuses,
        };
      }
      notify();
      return;
    }

    if (phase === "error") {
      scanPhase = "ready";
      fullScanActive = false;
      scanProgress = scanProgress
        ? {
            ...scanProgress,
            phase: "error",
            message: payload.message || "Scan failed",
          }
        : null;
      notify();
      return;
    }

    if (phase === "done") {
      scanPhase = "ready";
      fullScanActive = false;
      scanProgress = null;
      notify();
      return;
    }
  },

  /** Merge scan/sync payload into registry — never wipes unrelated files. */
  mergeScanResults(
    files: Array<{
      path: string;
      errors?: number;
      warnings?: number;
      items?: Array<{ line: number; column: number; message: string; severity: string }>;
    }>,
    scanId?: number,
  ): void {
    if (scanId !== undefined && scanId !== activeScanId) return;
    for (const file of files) {
      applyFileResult({
        path: file.path,
        errors: file.errors ?? 0,
        warnings: file.warnings ?? 0,
        items: file.items,
      });
    }
    scanPhase = "ready";
    fullScanActive = false;
    scanProgress = null;
    notify();
  },

  /** Merge live verse-lsp results from another window / cache persist.
   * Clean payloads clear that file — otherwise a fixed buffer stays red in every
   * window that never received the local publishDiagnostics push. */
  mergeLiveResults(
    files: Array<{
      path: string;
      errors?: number;
      warnings?: number;
      items?: Array<{ line: number; column: number; message: string; severity: string }>;
    }>,
  ): void {
    for (const file of files) {
      const errors = file.errors ?? 0;
      const warnings = file.warnings ?? 0;
      const key = registryKey(file.path);
      liveUpdateAt.set(key, Date.now());
      setEntry(key, {
        errors,
        warnings,
        items: normalizeScanItems(file.items),
      });
    }
    notify();
  },

  /** Apply an AUTHORITATIVE backend snapshot (replace=true syncs). The backend's
   * session state is reconciled against disk on every read — deleted/moved files are
   * pruned, unscanned files keep their last-known results — so this payload is the
   * whole truth: rebuild every content/ entry from it. This is what finally clears
   * ghost Problems for files that no longer exist (the old add-only merge kept them
   * forever because only a live LSP push could remove an entry).
   *
   * Files the snapshot marks clean drop sticky live errors too — otherwise a
   * one-shot false-positive from an open tab outlived every full project rescan.
   * Live-cleared files (liveUpdateAt set, no entry) still reject scan-found errors. */
  setScanResults(
    files: Array<{
      path: string;
      errors: number;
      warnings: number;
      items?: Array<{ line: number; column: number; message: string; severity: string }>;
    }>,
    scanId?: number,
  ): void {
    if (scanId !== undefined && scanId !== activeScanId) return;
    const cleanKeys = new Set<string>();
    for (const file of files) {
      if ((file.errors ?? 0) === 0 && (file.warnings ?? 0) === 0) {
        cleanKeys.add(registryKey(file.path));
      }
    }
    for (const key of cleanKeys) {
      liveUpdateAt.delete(key);
    }
    const liveErrors = new Map<string, FileDiagnosticEntry>();
    for (const [key, entry] of entries) {
      if (!key.startsWith("content/") || !liveUpdateAt.has(key) || cleanKeys.has(key)) continue;
      liveErrors.set(key, entry);
    }
    for (const key of [...entries.keys()]) {
      if (key.startsWith("content/")) entries.delete(key);
    }
    for (const file of files) {
      if (file.errors === 0 && file.warnings === 0) continue;
      const key = registryKey(file.path);
      if (liveErrors.has(key)) continue;
      // Live-cleared (liveUpdateAt set, no entry): keep clear — stale cache must not
      // paint the file tree red after the open buffer already went clean.
      if (liveUpdateAt.has(key)) continue;
      const items = normalizeScanItems(file.items);
      setEntry(key, { errors: file.errors, warnings: file.warnings, items });
    }
    for (const [key, entry] of liveErrors) {
      setEntry(key, entry);
    }
    scanPhase = "ready";
    scanProgress = null;
    notify();
  },

  getSummary(path: string): FileDiagnosticSummary | undefined {
    for (const key of registryLookupKeys(path)) {
      const entry = entries.get(key);
      if (entry) return { errors: entry.errors, warnings: entry.warnings };
    }
    return undefined;
  },

  /** Sum diagnostics for all files under a folder (VS Code explorer folder coloring). */
  getDescendantSummary(dirPath: string): FileDiagnosticSummary {
    const dir = registryKey(dirPath);
    const prefix = dir.endsWith("/") ? dir : `${dir}/`;
    let errors = 0;
    let warnings = 0;
    for (const [key, entry] of entries) {
      if (key.startsWith(prefix)) {
        errors += entry.errors;
        warnings += entry.warnings;
      }
    }
    return { errors, warnings };
  },

  /** File/folder renamed/deleted on disk: purge its diagnostics everywhere.
   * Folder deletes also drop every descendant — otherwise Problems keeps ghost
   * rows for files that vanished with the folder. */
  removeFile(path: string): void {
    for (const key of registryLookupKeys(path)) {
      entries.delete(key);
      liveUpdateAt.delete(key);
    }
    const dir = registryKey(path);
    if (dir) {
      const prefix = dir.endsWith("/") ? dir : `${dir}/`;
      for (const key of [...entries.keys()]) {
        if (!key.startsWith(prefix)) continue;
        entries.delete(key);
        liveUpdateAt.delete(key);
      }
    }
    notify();
  },

  /** Stored diagnostic items for one file — lets a remounting editor restore its
   * squiggles without any LSP request (VS Code marker persistence). */
  getItems(path: string): FileDiagnosticItem[] {
    for (const key of registryLookupKeys(path)) {
      const entry = entries.get(key);
      if (entry) return entry.items;
    }
    return [];
  },

  getAllSummaries(): ReadonlyMap<string, FileDiagnosticSummary> {
    const out = new Map<string, FileDiagnosticSummary>();
    for (const [key, entry] of entries) {
      out.set(key, { errors: entry.errors, warnings: entry.warnings });
    }
    return out;
  },

  getTotals(): { errors: number; warnings: number } {
    let errors = 0;
    let warnings = 0;
    for (const entry of entries.values()) {
      errors += entry.errors;
      warnings += entry.warnings;
    }
    return { errors, warnings };
  },

  getAllFileProblems(): FileProblemsGroup[] {
    const groups: FileProblemsGroup[] = [];
    for (const [path, entry] of entries) {
      if (!path.startsWith("content/")) continue;
      groups.push({
        path,
        errors: entry.errors,
        warnings: entry.warnings,
        items: entry.items,
      });
    }
    groups.sort((a, b) => {
      if (a.errors !== b.errors) return b.errors - a.errors;
      if (a.warnings !== b.warnings) return b.warnings - a.warnings;
      return a.path.localeCompare(b.path);
    });
    return groups;
  },

  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

// DevTools helper: window.__verseLspProblems() → registry snapshot for debugging the
// Problems badge/panel (why is it empty / what does it think the project contains).
if (typeof window !== "undefined") {
  (window as unknown as { __verseLspProblems?: () => unknown }).__verseLspProblems = () => ({
    totals: fileDiagnosticRegistry.getTotals(),
    entryCount: entries.size,
    scanPhase,
    checking: [...checkingFiles],
    files: fileDiagnosticRegistry.getAllFileProblems().slice(0, 20),
  });
}

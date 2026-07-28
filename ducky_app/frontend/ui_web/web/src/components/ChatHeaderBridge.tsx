import { useCallback, useEffect, useMemo, useState } from "react";

import { useRegisterAppHeaderActions } from "../contexts/AppHeaderActionsContext";

import { getApi } from "../hooks/usePanelApi";

import { useVerseEditor } from "../verse-editor";

import { useVerseProjectDiagnostics } from "../verse-editor/hooks/useVerseProjectDiagnostics";

import { requestProjectDiagnosticsScan } from "../verse-editor/diagnostics/requestProjectScan";

import { refreshOpenFileDiagnosticsNow } from "../verse-editor/diagnostics/refreshOpenFileDiagnostics";

import { fileDiagnosticRegistry } from "../verse-editor/lsp/fileDiagnosticRegistry";

import { isVerseFile } from "../verse-editor/utils/isVerseFile";

import type { EditorTab } from "../types/panel";

interface ChatHeaderBridgeProps {
  activeFilePath?: string;
  projectPath: string;
  /** Open editor tabs — reserved for callers; unused after vim moved to plugin. */
  openTabs?: EditorTab[];
}

export function ChatHeaderBridge({ activeFilePath, projectPath }: ChatHeaderBridgeProps) {
  const verseEditor = useVerseEditor();
  const { clearHistoryPreview } = verseEditor;
  const { setHeaderActions } = useRegisterAppHeaderActions();
  const [saving, setSaving] = useState(false);
  const [diagRev, setDiagRev] = useState(0);
  const bumpDiagnostics = useCallback(() => setDiagRev((n) => n + 1), []);

  useVerseProjectDiagnostics(projectPath, bumpDiagnostics);

  const dirty = activeFilePath ? verseEditor.isPathDirty(activeFilePath) : false;
  const showFileActions = !!activeFilePath && isVerseFile(activeFilePath);

  useEffect(() => {
    if (activeFilePath) clearHistoryPreview(activeFilePath);
  }, [activeFilePath, clearHistoryPreview]);

  const handleSave = useCallback(async () => {
    if (!activeFilePath || saving) return;

    setSaving(true);
    try {
      await verseEditor.savePath(activeFilePath);
    } finally {
      setSaving(false);
    }
  }, [activeFilePath, saving, verseEditor]);

  const handleNavigate = useCallback(
    (path: string, line: number, column: number) => {
      const norm = path.replace(/\\/g, "/").toLowerCase();
      if (!norm.startsWith("content/") || !norm.endsWith(".verse")) {
        return;
      }

      const name = path.split("/").pop() || path;
      verseEditor.openFileAt(path, name, { activate: true });
      verseEditor.requestReveal(path, line, column);
    },
    [verseEditor],
  );

  const handleRefreshProblems = useCallback(async () => {
    const path = projectPath.trim();
    if (!path) return;

    const dirtyPaths = [...verseEditor.dirtyPaths];
    if (dirtyPaths.length) {
      await Promise.all(dirtyPaths.map((p) => verseEditor.savePath(p)));
    }

    requestProjectDiagnosticsScan(path, { full: true });
    bumpDiagnostics();
  }, [projectPath, bumpDiagnostics, verseEditor]);

  const handleRefreshActiveFile = useCallback(() => {
    const root = projectPath.trim();
    if (!root || !activeFilePath || !isVerseFile(activeFilePath)) return;

    refreshOpenFileDiagnosticsNow(root, activeFilePath, true);
    bumpDiagnostics();
  }, [projectPath, activeFilePath, bumpDiagnostics]);

  const handleStopScan = useCallback(() => {
    fileDiagnosticRegistry.endScan();
    bumpDiagnostics();
    void getApi()?.stop_verse_diagnostics_scan(projectPath.trim());
  }, [projectPath, bumpDiagnostics]);

  const problemsData = useMemo(() => {
    void diagRev;

    const scanProgress = fileDiagnosticRegistry.getScanProgress();
    const totals = fileDiagnosticRegistry.getTotals();
    const files = fileDiagnosticRegistry.getAllFileProblems();
    const fullScanActive = fileDiagnosticRegistry.isFullScanInProgress();
    const scanInProgress = fileDiagnosticRegistry.isScanInProgress();
    const fileCheckInProgress = fileDiagnosticRegistry.isFileCheckInProgress();

    let status: "scanning" | "ok" | "warning" | "error" = "ok";
    if (scanInProgress || fileCheckInProgress) {
      status = "scanning";
    } else if (totals.errors > 0) {
      status = "error";
    } else if (totals.warnings > 0) {
      status = "warning";
    }

    return { status, totals, files, scanProgress, fullScanActive, scanInProgress, fileCheckInProgress };
  }, [diagRev]);

  const hasProject = !!projectPath.trim();

  useEffect(() => {
    setHeaderActions({
      save: showFileActions
        ? {
            dirty,
            saving,
            onSave: () => void handleSave(),
          }
        : null,
      problems: hasProject
        ? {
            status: problemsData.status,
            errorCount: problemsData.totals.errors,
            warningCount: problemsData.totals.warnings,
            files: problemsData.files,
            scanProgress: problemsData.scanProgress,
            onNavigate: handleNavigate,
            onRefresh: () => void handleRefreshProblems(),
            onRefreshActiveFile: showFileActions ? () => void handleRefreshActiveFile() : null,
            activeFileName: showFileActions ? activeFilePath!.split("/").pop() || null : null,
            onStopScan: () => void handleStopScan(),
            fullScanActive: problemsData.fullScanActive,
            scanInProgress: problemsData.scanInProgress,
            fileCheckInProgress: problemsData.fileCheckInProgress,
          }
        : null,
    });
  }, [
    activeFilePath,
    showFileActions,
    dirty,
    saving,
    handleSave,
    hasProject,
    problemsData,
    handleNavigate,
    handleRefreshProblems,
    handleRefreshActiveFile,
    handleStopScan,
    setHeaderActions,
  ]);

  useEffect(
    () => () => {
      setHeaderActions({ save: null, problems: null });
    },
    [setHeaderActions],
  );

  return null;
}

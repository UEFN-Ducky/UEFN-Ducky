import { useEffect, useRef } from "react";
import type { AgentEvent } from "../../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { getApi } from "../../hooks/usePanelApi";
import { onProjectSwitch } from "../../hooks/projectSwitchController";
import { stopLsp } from "../api/verseEditorApi";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";
import { releaseVerseLspSession } from "../lsp/verseLspSession";
import { purgeDeletedFileState } from "../diagnostics/purgeDeletedFile";
import { cancelAllOpenFileDiagnosticRefreshes } from "../diagnostics/refreshOpenFileDiagnostics";
import { normProjectRoot, requestProjectDiagnosticsScan } from "../diagnostics/requestProjectScan";
import {
  isVerseDiagnosticsAutoCheckEnabled,
  isVerseDiagnosticsCacheEnabled,
} from "../diagnostics/verseDiagnosticsSettings";

type ScanFilePayload = {
  path: string;
  errors: number;
  warnings: number;
  items?: Array<{ line: number; column: number; message: string; severity: string }>;
};

/** On open: restore cache for instant UI, then background-scan (we clear problems on switch). */
export function useVerseProjectDiagnostics(projectPath: string, onChange: () => void): void {
  const projectRef = useRef(projectPath);
  projectRef.current = projectPath;

  useEffect(() => {
    installAgentEventBus();
    return fileDiagnosticRegistry.subscribe(onChange);
  }, [onChange]);

  // Centralized per-project teardown: the switch controller fires this ONCE, at the
  // earliest signal that the root changed, so the Problems panel + verse-lsp session can
  // never keep the previous project's data. (Previously each project's effect re-derived
  // "did the root change?" and raced the switch.)
  useEffect(() => {
    return onProjectSwitch(() => {
      // Stop pending per-file refresh timers first — otherwise a scheduled recheck for an
      // old-project tab fires after the swap, finds its doc "not open", and churns the LSP.
      cancelAllOpenFileDiagnosticRefreshes();
      fileDiagnosticRegistry.clear();
      fileDiagnosticRegistry.endScan();
      releaseVerseLspSession();
      void stopLsp();
    });
  }, []);

  useEffect(() => {
    const watchdog = window.setInterval(() => {
      fileDiagnosticRegistry.tickScanWatchdog();
      onChange();
    }, 2000);
    return () => window.clearInterval(watchdog);
  }, [onChange]);

  useEffect(() => {
    const handler = (event: AgentEvent) => {
      if (event.type === "file_renamed" || event.type === "file_deleted") {
        const ev = event as AgentEvent & { old_path?: string };
        if (ev.old_path) {
          purgeDeletedFileState(ev.old_path);
        }
        return;
      }

      if (event.type === "verse_diagnostics_progress") {
        const ev = event as AgentEvent & {
          project_root?: string;
          phase?: string;
          index?: number;
          total?: number;
          path?: string;
          paths?: string[];
          message?: string;
          file?: ScanFilePayload;
        };
        if (ev.project_root) {
          if (normProjectRoot(ev.project_root) !== normProjectRoot(projectRef.current)) return;
        }
        fileDiagnosticRegistry.applyScanProgress(ev);
        if (ev.phase === "error") {
          void getApi()
            ?.load_verse_diagnostics_cache(projectRef.current)
            .then((cached) => {
              if (Array.isArray(cached.files) && cached.files.length > 0) {
                fileDiagnosticRegistry.mergeScanResults(cached.files as ScanFilePayload[]);
                onChange();
              }
            })
            .catch(() => {});
        }
        return;
      }

      if (event.type !== "verse_diagnostics_sync") return;
      const ev = event as AgentEvent & {
        files?: ScanFilePayload[];
        project_root?: string;
        replace?: boolean;
        live?: boolean;
      };
      if (ev.project_root) {
        if (normProjectRoot(ev.project_root) !== normProjectRoot(projectRef.current)) return;
      }
      const files = ev.files;
      if (!Array.isArray(files)) return;
      if (ev.replace) {
        fileDiagnosticRegistry.setScanResults(files);
      } else if (ev.live) {
        // Live open/recheck persist: clean results clear Problems (cache zombies).
        fileDiagnosticRegistry.mergeLiveResults(files);
      } else {
        fileDiagnosticRegistry.mergeScanResults(files);
      }
    };
    return subscribeAgentEvents(handler);
  }, []);

  useEffect(() => {
    const path = projectPath.trim();
    if (!path) {
      fileDiagnosticRegistry.clear();
      fileDiagnosticRegistry.endScan();
      return;
    }

    // Teardown on switch is owned by onProjectSwitch above. After clear we must
    // re-check: hydrate cache for instant badge, then incremental scan (stale /
    // never-scanned files) via ephemeral verse-lsp — not the live editor session.
    void (async () => {
      const api = getApi();
      if (!api) return;
      let staleCount = -1;
      try {
        const cached = await api.load_verse_diagnostics_cache(path);
        if (normProjectRoot(projectRef.current) !== normProjectRoot(path)) return;
        staleCount = typeof cached.stale_count === "number" ? cached.stale_count : -1;
        if (isVerseDiagnosticsCacheEnabled() && Array.isArray(cached.files) && cached.files.length > 0) {
          fileDiagnosticRegistry.hydrate(cached.files as ScanFilePayload[]);
          onChange();
        }
      } catch {
        // ignore cache load errors
      }
      if (normProjectRoot(projectRef.current) !== normProjectRoot(path)) return;
      if (!isVerseDiagnosticsAutoCheckEnabled()) return;
      // Disk/session cache already fresh — skip the Problems spinner + verse-lsp pass.
      if (staleCount === 0) return;
      requestProjectDiagnosticsScan(path);
      onChange();
    })();
  }, [projectPath, onChange]);
}

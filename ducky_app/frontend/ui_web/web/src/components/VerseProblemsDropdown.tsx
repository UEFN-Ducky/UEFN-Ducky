import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { HeaderProblemsAction } from "../contexts/AppHeaderActionsContext";
import { useProblemsMenuOpen } from "../contexts/AppHeaderActionsContext";
import type { ScanFileStatus } from "../verse-editor/lsp/fileDiagnosticRegistry";
import { Icons } from "../icons/Icons";
import { TruncatedText } from "./TruncatedText";
import { VerseProblemsDuckyMenu } from "./VerseProblemsDuckyMenu";

type VerseProblemsDropdownProps = HeaderProblemsAction & {
  onOpenChange?: (open: boolean) => void;
};

const MENU_WIDTH = 400;
const MENU_GAP = 6;

function formatTitle(
  status: HeaderProblemsAction["status"],
  errorCount: number,
  warningCount: number,
  scanMessage?: string,
  scanInProgress?: boolean,
  fileCheckInProgress?: boolean,
  incrementalTotal?: number,
): string {
  if (fileCheckInProgress && !scanInProgress) return "Checking current file…";
  if (status === "scanning") {
    if (scanMessage) return scanMessage;
    if (incrementalTotal && incrementalTotal > 0) {
      return `Checking ${incrementalTotal} updated file${incrementalTotal === 1 ? "" : "s"}…`;
    }
    return "Checking Verse project…";
  }
  if (errorCount > 0 || warningCount > 0) {
    const parts: string[] = [];
    if (errorCount > 0) parts.push(`${errorCount} error${errorCount === 1 ? "" : "s"}`);
    if (warningCount > 0) parts.push(`${warningCount} warning${warningCount === 1 ? "" : "s"}`);
    return parts.join(", ");
  }
  return "No problems";
}

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.right - MENU_WIDTH;
  if (left < 8) left = 8;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  return { top: rect.bottom + MENU_GAP, left };
}

function scanStatusLabel(status: ScanFileStatus): string {
  if (status === "active") return "Checking…";
  if (status === "done") return "Done";
  return "Pending";
}

export function VerseProblemsDropdown({
  status,
  errorCount,
  warningCount,
  files,
  scanProgress,
  fullScanActive,
  scanInProgress,
  fileCheckInProgress,
  onNavigate,
  onRefresh,
  onRefreshActiveFile,
  activeFileName,
  onStopScan,
  onOpenChange,
}: VerseProblemsDropdownProps) {
  const { problemsMenuOpen: open, setProblemsMenuOpen } = useProblemsMenuOpen();

  const setOpenState = (next: boolean) => {
    setProblemsMenuOpen(next);
    onOpenChange?.(next);
  };
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const totalCount = errorCount + warningCount;
  const scanMessage = scanProgress?.message;
  const activityBusy = scanInProgress || fileCheckInProgress;
  const showScanList = scanInProgress && scanProgress && scanProgress.paths.length > 0;
  const incrementalTotal = !fullScanActive && scanInProgress ? scanProgress?.total : undefined;
  const title = formatTitle(
    activityBusy ? "scanning" : status,
    errorCount,
    warningCount,
    scanMessage,
    scanInProgress,
    fileCheckInProgress,
    incrementalTotal,
  );
  const progressPct =
    scanProgress && scanProgress.total > 0
      ? Math.min(100, Math.round((scanProgress.done / scanProgress.total) * 100))
      : 0;

  const problemsByPath = new Map(files.map((f) => [f.path.toLowerCase(), f]));

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuPos(null);
      return;
    }
    const update = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      setMenuPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open || !scanInProgress || !listRef.current) return;
    const active = listRef.current.querySelector(".verse-problems-scan-row.is-active");
    active?.scrollIntoView({ block: "nearest" });
  }, [open, scanInProgress, scanProgress?.currentPath, scanProgress?.done]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      if ((target as Element).closest?.(".verse-problems-ducky-dropdown--portaled")) return;
      setOpenState(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenState(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const triggerClass = activityBusy
    ? fileCheckInProgress && !scanInProgress
      ? "verse-problems-trigger--checking"
      : "verse-problems-trigger--scanning"
    : status === "error"
      ? "verse-problems-trigger--error"
      : status === "warning"
        ? "verse-problems-trigger--warning"
        : "verse-problems-trigger--ok";

  const menu =
    open && menuPos ? (
      <div
        ref={menuRef}
        className="verse-problems-menu verse-problems-menu--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left }}
      >
        <div className="verse-problems-header">
          <div className="verse-problems-header-row">
            <span className="verse-problems-title">
              {fullScanActive
                ? "Checking project…"
                : scanInProgress
                  ? "Checking updated files…"
                  : totalCount > 0
                    ? `Problems (${totalCount})`
                    : "Problems"}
            </span>
            {onRefreshActiveFile ? (
              <button
                type="button"
                className={`icon-btn verse-problems-refresh-btn${
                  fileCheckInProgress && !scanInProgress ? " is-spinning" : ""
                }`}
                title={
                  activityBusy
                    ? "Check in progress…"
                    : `Recheck current file${activeFileName ? ` (${activeFileName})` : ""}`
                }
                aria-label={activityBusy ? "Check in progress" : "Recheck current file"}
                disabled={activityBusy}
                onClick={(e) => {
                  e.stopPropagation();
                  onRefreshActiveFile();
                }}
              >
                <Icons.File />
              </button>
            ) : null}
            <button
              type="button"
              className={`icon-btn verse-problems-refresh-btn${scanInProgress ? " is-spinning" : ""}`}
              title={scanInProgress ? "Scan in progress…" : "Refresh entire project"}
              aria-label={scanInProgress ? "Scan in progress" : "Refresh entire project"}
              disabled={scanInProgress}
              onClick={(e) => {
                e.stopPropagation();
                onRefresh();
              }}
            >
              <Icons.Refresh />
            </button>
            {scanInProgress ? (
              <button
                type="button"
                className="icon-btn verse-problems-refresh-btn verse-problems-stop-btn"
                title="Stop scan"
                aria-label="Stop scan"
                onClick={(e) => {
                  e.stopPropagation();
                  onStopScan();
                }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
              </button>
            ) : null}
            <VerseProblemsDuckyMenu
              payload={{ files, errorCount, warningCount }}
              disabled={activityBusy}
              onSent={() => setOpenState(false)}
            />
          </div>
          <span className="verse-problems-subtitle">{title}</span>
          {scanInProgress && scanProgress && scanProgress.total > 0 ? (
            <div className="verse-problems-progress">
              <div className="verse-problems-progress-bar" aria-hidden>
                <div
                  className="verse-problems-progress-fill"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <span className="verse-problems-progress-label">
                {scanProgress.done}/{scanProgress.total} files
              </span>
            </div>
          ) : null}
        </div>
        <div className="verse-problems-list" ref={listRef}>
          {showScanList ? (
            <>
              {scanProgress.paths.map((path) => {
                const fileStatus = scanProgress.fileStatuses.get(path) || "pending";
                const fileName = path.split("/").pop() || path;
                const fileProblems = problemsByPath.get(path.toLowerCase());
                return (
                  <div
                    key={path}
                    className={`verse-problems-scan-row verse-problems-scan-row--${fileStatus}${
                      scanProgress.currentPath === path ? " is-active" : ""
                    }`}
                  >
                    <span className={`verse-problems-scan-icon verse-problems-scan-icon--${fileStatus}`} aria-hidden>
                      {fileStatus === "active" ? (
                        <span className="verse-problems-spinner" />
                      ) : fileStatus === "done" ? (
                        fileProblems && (fileProblems.errors > 0 || fileProblems.warnings > 0) ? (
                          "!"
                        ) : (
                          <Icons.Check />
                        )
                      ) : (
                        "·"
                      )}
                    </span>
                    <div className="verse-problems-scan-main">
                      <TruncatedText className="verse-problems-scan-path" title={path}>
                        {fileName}
                      </TruncatedText>
                      <span className="verse-problems-scan-state">
                        {fileStatus === "done" && fileProblems
                          ? fileProblems.errors > 0 || fileProblems.warnings > 0
                            ? `${fileProblems.errors} err · ${fileProblems.warnings} warn`
                            : "OK"
                          : scanStatusLabel(fileStatus)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </>
          ) : scanInProgress ? (
            <div className="verse-problems-empty">{scanMessage || "Starting Verse project scan…"}</div>
          ) : totalCount === 0 ? (
            <div className="verse-problems-empty verse-problems-empty--ok">No errors or warnings in this project.</div>
          ) : (
            files.map((file) => {
              const fileName = file.path.split("/").pop() || file.path;
              return (
                <div key={file.path} className="verse-problems-file-group">
                  <TruncatedText className="verse-problems-file-name" title={file.path}>
                    {fileName}
                  </TruncatedText>
                  {file.items.map((item, i) => (
                    <button
                      key={`${file.path}:${item.line}:${item.column}:${i}`}
                      type="button"
                      className={`verse-problems-item verse-problems-item--${item.severity}`}
                      onClick={() => {
                        onNavigate(file.path, item.line, item.column);
                        setOpenState(false);
                      }}
                    >
                      <span className="verse-problems-item-line">Ln {item.line}</span>
                      <TruncatedText className="verse-problems-item-msg" title={item.message}>
                        {item.message}
                      </TruncatedText>
                    </button>
                  ))}
                </div>
              );
            })
          )}
        </div>
      </div>
    ) : null;

  return (
    <div className="verse-problems-root">
      <button
        ref={triggerRef}
        type="button"
        className={`verse-problems-trigger ${open ? "is-active" : ""} ${triggerClass}`}
        title={title}
        aria-label={`Problems: ${title}`}
        aria-busy={activityBusy}
        onClick={() => setOpenState(!open)}
      >
        {activityBusy ? (
          <span className="verse-problems-spinner" aria-hidden />
        ) : status === "ok" ? (
          <Icons.Check />
        ) : status === "error" ? (
          <Icons.ErrorCircle />
        ) : (
          <Icons.AlertTriangle />
        )}
      </button>

      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}

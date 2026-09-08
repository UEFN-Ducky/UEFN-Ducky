import { useCallback, useEffect, useMemo, useState } from "react";

import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import type { ChangesetRevertResult, ChangesetRunDto, SessionFile } from "../types/panel";
import {
  changesetFilePaths,
  changesetRunSummary,
  groupChangesetEntries,
  sortRunsNewestFirst,
  statusLabel,
  type ChangesetFileRow,
} from "../utils/changesetGrouping";
import { formatSavedAt } from "../utils/formatSavedAt";
import { FileTypeIcon } from "../verse-editor/components/FileTypeIcon";
import { basename } from "../verse-editor/utils/isVerseFile";
import { shortModelLabel } from "./groupMemberHover";
import { Modal } from "./Modal";
import { ToolFileEditDiff } from "./ToolFileEditDiff";

interface ChangesetPanelProps {
  convId: string;
  /** Group hub: list every member's runs instead of one chat's. */
  isGroup?: boolean;
  agentRunning?: boolean;
  /** Pre-journal chats: the tool-call scan, shown when no runs exist. */
  fallbackFiles: SessionFile[];
  onOpenFile?: (path: string, name: string) => void;
  /** Files currently shown (for the accordion title). */
  onFileCount?: (count: number) => void;
}

interface DiffView {
  path: string;
  before: string;
  after: string;
  kind: "write" | "create";
}

const LIVE_EVENT_TYPES = new Set(["tool_done", "agent_stopped", "files_reverted", "file_guard"]);

/**
 * Per-run ledger of what each ducky wrote (Context panel → Files). Backed by the
 * change journal: every row can be diffed and reverted; conflicts and out-of-lane
 * writes are badged. Falls back to the session file scan for older chats.
 */
export function ChangesetPanel({
  convId,
  isGroup = false,
  agentRunning = false,
  fallbackFiles,
  onOpenFile,
  onFileCount,
}: ChangesetPanelProps) {
  const { confirm } = useConfirmModal();
  const [runs, setRuns] = useState<ChangesetRunDto[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyRun, setBusyRun] = useState("");
  const [diff, setDiff] = useState<DiffView | null>(null);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.list_changesets) {
      setRuns([]);
      return;
    }
    setLoading(true);
    try {
      const rows = await api.list_changesets(isGroup ? "" : convId, isGroup ? convId : "", 50);
      setRuns(sortRunsNewestFirst(Array.isArray(rows) ? rows : []));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [convId, isGroup]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Any write, stop, revert or guard for this chat (or, on a hub, any member) refreshes.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribeAgentEvents((event) => {
      if (!LIVE_EVENT_TYPES.has(event.type)) return;
      if (!isGroup && event.conv_id && event.conv_id !== convId) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), 400);
    });
    return () => {
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [convId, isGroup, refresh]);

  const filePaths = useMemo(() => changesetFilePaths(runs), [runs]);
  useEffect(() => {
    onFileCount?.(runs.length > 0 ? filePaths.length : fallbackFiles.length);
  }, [runs.length, filePaths.length, fallbackFiles.length, onFileCount]);

  const reportRevert = useCallback(
    async (label: string, result: ChangesetRevertResult, retry: () => Promise<ChangesetRevertResult | null>) => {
      if (result.errors.length) setError(result.errors.join("; "));
      if (result.skipped_modified.length) {
        const names = result.skipped_modified.map((s) => basename(s.path)).join(", ");
        const force = await confirm({
          title: "Some files changed since",
          message: `${names} ${result.skipped_modified.length === 1 ? "was" : "were"} edited after this ${label}. Restore ${result.skipped_modified.length === 1 ? "it" : "them"} anyway? Later edits will be lost.`,
          confirmLabel: "Revert anyway",
          danger: true,
        });
        if (force) {
          const forced = await retry();
          if (forced?.errors.length) setError(forced.errors.join("; "));
        }
      }
      await refresh();
    },
    [confirm, refresh],
  );

  const revertRun = useCallback(
    async (run: ChangesetRunDto) => {
      const api = getApi();
      if (!api?.revert_changeset) return;
      const summary = changesetRunSummary(run);
      const who = run.ducky_name || "this run";
      const ok = await confirm({
        title: "Revert run",
        message: `Restore the ${summary.files} file${summary.files === 1 ? "" : "s"} ${who} changed to their content before this run? The revert is recorded and can itself be reverted.`,
        confirmLabel: "Revert",
        danger: true,
      });
      if (!ok) return;
      setBusyRun(run.run_id);
      setError("");
      try {
        const result = await api.revert_changeset(run.run_id);
        await reportRevert("run", result, () => api.revert_changeset!(run.run_id, true));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyRun("");
      }
    },
    [confirm, reportRevert],
  );

  const revertFile = useCallback(
    async (run: ChangesetRunDto, row: ChangesetFileRow) => {
      const api = getApi();
      if (!api?.revert_changeset_entry) return;
      const ok = await confirm({
        title: "Revert file",
        message: `Restore ${basename(row.path)} to its content before ${run.ducky_name || "this run"} touched it?`,
        confirmLabel: "Revert",
        danger: true,
      });
      if (!ok) return;
      setBusyRun(run.run_id);
      setError("");
      try {
        const result = await api.revert_changeset_entry(run.run_id, row.lastSeq);
        await reportRevert("edit", result, () => api.revert_changeset_entry!(run.run_id, row.lastSeq, true));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyRun("");
      }
    },
    [confirm, reportRevert],
  );

  const openDiff = useCallback(async (run: ChangesetRunDto, row: ChangesetFileRow) => {
    const api = getApi();
    if (!api?.get_changeset_entry_contents) return;
    try {
      const first = await api.get_changeset_entry_contents(run.run_id, row.firstSeq);
      const last = row.lastSeq === row.firstSeq ? first : await api.get_changeset_entry_contents(run.run_id, row.lastSeq);
      setDiff({
        path: row.path,
        before: first.before ?? "",
        after: last.after ?? "",
        kind: row.op === "create" ? "create" : "write",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  if (runs.length === 0) {
    if (loading) return <div className="changeset-empty">Loading changes…</div>;
    if (fallbackFiles.length === 0) {
      return <div className="context-usage-panel-files-empty">No files edited in this chat yet.</div>;
    }
    return (
      <div className="context-usage-panel-files-list">
        {fallbackFiles.map((file) => (
          <button
            key={file.path}
            type="button"
            onClick={() => onOpenFile?.(file.path, basename(file.path))}
            className={`context-usage-panel-file-btn${onOpenFile ? " context-usage-panel-file-btn--clickable" : ""}`}
          >
            <span className="context-usage-panel-file-icon">
              <FileTypeIcon path={file.path} size={13} />
            </span>
            <span className="context-usage-panel-file-name">{basename(file.path)}</span>
            {file.lines_added != null && file.lines_added > 0 && (
              <span className="context-usage-panel-file-added">+{file.lines_added}</span>
            )}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="changeset-panel">
      {error ? <div className="changeset-error">{error}</div> : null}
      {runs.map((run) => {
        const rows = groupChangesetEntries(run);
        const summary = changesetRunSummary(run);
        const busy = busyRun === run.run_id;
        const canRevert = !summary.running && !summary.reverted && !busy;
        const meta = [shortModelLabel(run.model || ""), formatSavedAt(Math.floor(run.started))].filter(Boolean).join(" · ");
        return (
          <section key={run.run_id} className="changeset-run">
            <div className="changeset-run-head">
              <span className="changeset-run-title">{run.ducky_name || (run.source === "revert" ? "Revert" : "Ducky")}</span>
              <span className={`changeset-status changeset-status--${run.status}`}>
                {statusLabel(run.status)}
                {summary.running && agentRunning ? "…" : ""}
              </span>
              {summary.conflicts > 0 ? (
                <span className="changeset-badge changeset-badge--conflict" title="Another run changed a file between this run's read and write">
                  {summary.conflicts} conflict{summary.conflicts === 1 ? "" : "s"}
                </span>
              ) : null}
              {summary.outOfLane > 0 ? (
                <span className="changeset-badge changeset-badge--lane" title="Written outside the member's lane (shadow mode)">
                  {summary.outOfLane} out of lane
                </span>
              ) : null}
              <span className="changeset-run-meta" title={run.run_id}>
                {meta}
              </span>
              {run.source !== "revert" ? (
                <button
                  type="button"
                  className="changeset-btn"
                  disabled={!canRevert}
                  title={summary.running ? "Wait for the run to finish" : "Restore every file this run changed"}
                  onClick={() => void revertRun(run)}
                >
                  Revert all
                </button>
              ) : null}
            </div>
            <div className="changeset-files">
              {rows.map((row) => (
                <div key={row.path} className={`changeset-file${row.reverted ? " changeset-file--reverted" : ""}`}>
                  <span className="context-usage-panel-file-icon">
                    <FileTypeIcon path={row.path} size={13} />
                  </span>
                  <button
                    type="button"
                    className="changeset-file-name"
                    title={row.fromPath ? `${row.fromPath} → ${row.path}` : row.path}
                    onClick={() => onOpenFile?.(row.path, basename(row.path))}
                  >
                    {basename(row.path)}
                  </button>
                  <span className="changeset-file-op">{row.op}</span>
                  {row.linesAdded > 0 || row.linesRemoved > 0 ? (
                    <span className="changeset-file-stats">
                      {row.linesAdded > 0 ? <span className="changeset-file-stat--add">+{row.linesAdded}</span> : null}
                      {row.linesAdded > 0 && row.linesRemoved > 0 ? " " : ""}
                      {row.linesRemoved > 0 ? <span className="changeset-file-stat--remove">−{row.linesRemoved}</span> : null}
                    </span>
                  ) : null}
                  {row.conflict ? (
                    <span
                      className="changeset-badge changeset-badge--conflict"
                      title={
                        row.conflict.kind === "stale_base"
                          ? `Changed by ${row.conflict.other_ducky || "another run"} after it was read`
                          : `Also being edited by ${row.conflict.other_ducky || "another run"}`
                      }
                    >
                      Conflict
                    </span>
                  ) : null}
                  {row.outOfLane ? (
                    <span className="changeset-badge changeset-badge--lane" title="Outside the member's write lane">
                      Out of lane
                    </span>
                  ) : null}
                  {row.reverted ? <span className="changeset-badge">Reverted</span> : null}
                  {row.hasDiff && !row.reverted ? (
                    <button type="button" className="changeset-btn" onClick={() => void openDiff(run, row)}>
                      Diff
                    </button>
                  ) : null}
                  {!row.reverted && run.source !== "revert" ? (
                    <button
                      type="button"
                      className="changeset-btn"
                      disabled={!canRevert}
                      onClick={() => void revertFile(run, row)}
                    >
                      Revert
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        );
      })}
      {diff ? (
        <Modal open onClose={() => setDiff(null)} title={basename(diff.path)} width={760} zIndex={100020}>
          <div className="changeset-diff-modal">
            <ToolFileEditDiff
              edit={{ path: diff.path, before: diff.before, after: diff.after, linesAdded: 0, linesRemoved: 0, kind: diff.kind }}
              onOpenFile={onOpenFile ? (path, name) => onOpenFile(path, name) : undefined}
              defaultExpanded
            />
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

import { Icons } from "../../icons/Icons";
import type { ChangesetRunDto } from "../../types/panel";
import type { ChangeRow } from "../../utils/changesetGrouping";
import { FileTypeIcon } from "../../verse-editor/components/FileTypeIcon";
import { basename } from "../../verse-editor/utils/isVerseFile";

export interface ChangeRowViewProps {
  run: ChangesetRunDto;
  row: ChangeRow;
  busy: boolean;
  /** This row's run is the one currently reverting — lock the whole row. */
  reverting?: boolean;
  expanded: boolean;
  /** Revert-source rows undo the undo (bring the original work back). */
  redo?: boolean;
  canRedo?: boolean;
  onToggleExpanded: () => void;
  onOpenFile?: (path: string, name: string) => void;
  onDiff: (seq?: number) => void;
  /** Blocked/failed rows: open the reason in a popup (the row itself stays one line). */
  onDetails?: () => void;
  onRevert: () => void;
  /** Undo one write in a collapsed row. Newest-first; later writes block. */
  onRevertStep?: (seq: number) => void;
  onRedo?: () => void;
  onSmartRevert?: () => void;
  highlighted?: boolean;
  /** Agent for this run is still writing — revert stays locked until Stop. */
  agentLive?: boolean;
  onContextMenu?: (e: React.MouseEvent) => void;
}

function timeOfDay(ts: number): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * One change in a run's timeline.
 *
 * A file row opens the file; an editor row deliberately does not — its path is a
 * `uefn://` target slot with no file behind it, so it gets no file icon and no
 * open handler. Blocked rows stay one line: click opens the reason in a popup;
 * they offer no revert because nothing happened, so there is nothing to undo.
 */
export function ChangeRowView({
  run,
  row,
  busy,
  reverting = false,
  expanded,
  redo = false,
  canRedo = false,
  onToggleExpanded,
  onOpenFile,
  onDiff,
  onDetails,
  onRevert,
  onRevertStep,
  onRedo,
  onSmartRevert,
  highlighted = false,
  agentLive = false,
  onContextMenu,
}: ChangeRowViewProps) {
  const blocked = row.outcome !== "ok";
  const openable = blocked ? Boolean(onDetails) : false;
  const collapsed = row.steps.length > 1;
  const live = agentLive || run.status === "running";
  const undoable =
    !blocked &&
    !row.reverted &&
    !(row.kind === "editor" && row.revertable === "none");
  const canRevert = undoable && !busy && !run.archived && !live;
  const showRevert = !blocked && !row.reverted;
  const canDiff = !blocked && row.hasDiff && !row.reverted;
  const revertTitle = !undoable
    ? row.reason || "Ducky has no recorded inverse for this one — undo it in UEFN"
    : run.archived
      ? "Archived — revert is locked. Unarchive first."
      : busy
      ? "Wait — a revert is already in progress"
      : live
        ? "Stop the agent first — this run is still going."
        : redo
          ? "Bring this change back"
          : "Undo this change. Redo brings it back.";

  const classes = [
    "changes-row",
    `changes-row--${row.kind}`,
    blocked ? `changes-row--${row.outcome}` : "",
    row.reverted ? "changes-row--reverted" : "",
    highlighted ? "changes-row--focus" : "",
    reverting ? "changes-row--busy" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      data-focus-seq={`${run.run_id}:${row.lastSeq}`}
      aria-busy={reverting || undefined}
      onContextMenu={reverting ? undefined : onContextMenu}
    >
      <div
        className={`changes-row-line${canDiff || openable ? " changes-row-line--openable" : ""}`}
        title={blocked ? row.reason : undefined}
        onClick={
          canDiff || openable
            ? (e) => {
                if ((e.target as HTMLElement).closest("button")) return;
                if (canDiff) onDiff();
                else onDetails?.();
              }
            : undefined
        }
      >
        <span className="changes-row-time">{timeOfDay(row.firstTs)}</span>
        {blocked ? (
          <span className="changes-row-verb changes-row-verb--blocked">
            {row.outcome === "blocked" ? "BLOCKED" : "FAILED"}
          </span>
        ) : (
          <span className="changes-row-verb">{row.kind === "file" ? row.op : row.verb}</span>
        )}

        {row.kind === "file" ? (
          <>
            <span className="changes-row-icon">
              <FileTypeIcon path={row.path} size={13} />
            </span>
            <button
              type="button"
              className="changes-row-name changes-row-name--file"
              title={row.fromPath ? `${row.fromPath} → ${row.path}` : row.path}
              onClick={() => onOpenFile?.(row.path, basename(row.path))}
            >
              {basename(row.path)}
            </button>
          </>
        ) : (
          <>
            <span className="changes-row-kind">{row.targetKind}</span>
            <span className="changes-row-name" title={row.slot}>
              {row.label}
            </span>
          </>
        )}

        <span className="changes-row-detail">
          {row.kind === "file"
            ? [
                row.linesAdded > 0 ? `+${row.linesAdded}` : "",
                row.linesRemoved > 0 ? `−${row.linesRemoved}` : "",
              ]
                .filter(Boolean)
                .join(" ")
            : row.detail}
        </span>

        {collapsed ? (
          <button
            type="button"
            className="changes-row-steps-toggle"
            onClick={onToggleExpanded}
            aria-expanded={expanded}
          >
            {row.steps.length} steps
          </button>
        ) : null}

        {row.kind === "file" && row.conflict ? (
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
        {row.kind === "file" && row.outOfLane ? (
          <span className="changeset-badge changeset-badge--lane">Out of lane</span>
        ) : null}
        {row.kind === "editor" && !blocked ? (
          <span
            className={`changeset-badge changeset-badge--${row.revertable}`}
            title={
              row.revertable === "auto"
                ? "Ducky recorded how to put this back"
                : row.reason || "Undo this one in UEFN"
            }
          >
            {row.revertable === "auto" ? "Auto" : row.revertable === "manual" ? "Manual" : "No undo"}
          </span>
        ) : null}
        {row.reverted ? <span className="changeset-badge">Reverted</span> : null}

        <span className="changes-row-actions">
          {canDiff ? (
            <button
              type="button"
              className="changeset-btn changeset-btn--icon"
              title={row.kind === "file" ? "Diff" : "Details"}
              aria-label={row.kind === "file" ? "Diff" : "Details"}
              onClick={() => onDiff()}
            >
              <Icons.Diff />
            </button>
          ) : null}
          {showRevert && !redo ? (
            <button
              type="button"
              className="changeset-btn changeset-btn--icon"
              disabled={!canRevert}
              title={revertTitle}
              aria-label="Revert"
              onClick={onRevert}
            >
              <Icons.Undo />
            </button>
          ) : null}
          {onSmartRevert && !blocked && !row.reverted ? (
            <button
              type="button"
              className="changeset-btn changeset-btn--icon"
              title="Smart Revert — pick a chat to unwind this"
              aria-label="Smart Revert"
              onClick={onSmartRevert}
            >
              <Icons.Sparkles />
            </button>
          ) : null}
          {redo && canRevert ? (
            <button
              type="button"
              className="changeset-btn changeset-btn--icon"
              title={revertTitle}
              aria-label="Redo"
              onClick={onRevert}
            >
              <Icons.Redo />
            </button>
          ) : null}
          {canRedo && onRedo ? (
            <button
              type="button"
              className="changeset-btn changeset-btn--icon"
              title="Redo"
              aria-label="Redo"
              onClick={onRedo}
            >
              <Icons.Redo />
            </button>
          ) : null}
        </span>
      </div>

      {expanded
        ? row.steps.map((step) => (
            <div
              key={step.seq}
              className={`changes-step${canDiff ? " changes-step--openable" : ""}`}
              role={canDiff ? "button" : undefined}
              onClick={canDiff ? () => onDiff(step.seq) : undefined}
            >
              <span className="changes-row-time">{timeOfDay(step.ts)}</span>
              <span className="changes-step-tool">{step.tool}</span>
              <span className="changes-step-summary">{step.summary}</span>
              {onRevertStep && canRevert && !step.reverted ? (
                <button
                  type="button"
                  className="changeset-btn changeset-btn--icon"
                  title="Revert this write"
                  aria-label={`Revert write ${step.seq}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRevertStep(step.seq);
                  }}
                >
                  <Icons.Undo />
                </button>
              ) : step.reverted ? (
                <span className="changeset-badge">Reverted</span>
              ) : null}
            </div>
          ))
        : null}
    </div>
  );
}

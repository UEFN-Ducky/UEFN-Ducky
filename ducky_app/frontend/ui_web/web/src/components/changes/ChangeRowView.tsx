import type { ChangesetRunDto } from "../../types/panel";
import type { ChangeRow } from "../../utils/changesetGrouping";
import { FileTypeIcon } from "../../verse-editor/components/FileTypeIcon";
import { basename } from "../../verse-editor/utils/isVerseFile";

export interface ChangeRowViewProps {
  run: ChangesetRunDto;
  row: ChangeRow;
  busy: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
  onOpenFile?: (path: string, name: string) => void;
  onDiff: () => void;
  onRevert: () => void;
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
 * open handler. Blocked rows carry their reason and offer no revert: nothing
 * happened, so there is nothing to undo.
 */
export function ChangeRowView({
  run,
  row,
  busy,
  expanded,
  onToggleExpanded,
  onOpenFile,
  onDiff,
  onRevert,
}: ChangeRowViewProps) {
  const blocked = row.outcome !== "ok";
  const collapsed = row.steps.length > 1;
  const canRevert = !blocked && !row.reverted && !busy && run.source !== "revert" && run.status !== "running";

  const classes = [
    "changes-row",
    `changes-row--${row.kind}`,
    blocked ? `changes-row--${row.outcome}` : "",
    row.reverted ? "changes-row--reverted" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <div className="changes-row-line">
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

        {!blocked && row.hasDiff && !row.reverted ? (
          <button type="button" className="changeset-btn" onClick={onDiff}>
            {row.kind === "file" ? "Diff" : "Details"}
          </button>
        ) : null}
        {canRevert && !(row.kind === "editor" && row.revertable === "none") ? (
          <button type="button" className="changeset-btn" onClick={onRevert}>
            Revert
          </button>
        ) : null}
      </div>

      {blocked && row.reason ? (
        <div className="changes-row-reason" title={row.reason}>
          {row.reason}
        </div>
      ) : null}

      {expanded
        ? row.steps.map((step) => (
            <div key={step.seq} className="changes-step">
              <span className="changes-row-time">{timeOfDay(step.ts)}</span>
              <span className="changes-step-tool">{step.tool}</span>
              <span className="changes-step-summary">{step.summary}</span>
            </div>
          ))
        : null}
    </div>
  );
}

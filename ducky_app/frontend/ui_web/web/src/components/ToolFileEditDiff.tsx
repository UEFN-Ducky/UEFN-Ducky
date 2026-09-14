import { useMemo, type KeyboardEvent, type MouseEvent } from "react";
import { Icons } from "../icons/Icons";
import { chatCollapseKey, useChatCollapseScope, useChatCollapseState } from "../hooks/useChatCollapseState";
import type { FileEditData } from "../types/panel";
import { buildFileEditDiff } from "../utils/fileEditDiff";
import { useVerseEditorOptional } from "../verse-editor/VerseEditorProvider";
import { basename } from "../verse-editor/utils/isVerseFile";

interface ToolFileEditDiffProps {
  edit: FileEditData;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  /** Start open (changeset review modal); chat cards start collapsed. */
  defaultExpanded?: boolean;
}

export function ToolFileEditDiff({ edit, onOpenFile, defaultExpanded = false }: ToolFileEditDiffProps) {
  const collapseScope = useChatCollapseScope();
  const [expanded, setExpanded] = useChatCollapseState(
    chatCollapseKey(collapseScope, defaultExpanded ? "file-diff-review" : "file-diff"),
    defaultExpanded,
  );
  const verseEditor = useVerseEditorOptional();
  const summary = useMemo(
    () => expanded ?
      buildFileEditDiff(
        edit.path,
        edit.before,
        edit.after,
        edit.linesAdded,
        edit.linesRemoved,
        edit.kind === "create" ? "create" : "write",
      ) : null,
    [edit, expanded],
  );

  const displayName = basename(edit.path);
  const hasChanges = edit.linesAdded > 0 || edit.linesRemoved > 0 || edit.kind === "create";

  const openFirstChange = () => {
    const diff = summary ?? buildFileEditDiff(
      edit.path, edit.before, edit.after, edit.linesAdded, edit.linesRemoved,
      edit.kind === "create" ? "create" : "write",
    );
    const line = diff.hunks.flatMap((h) => h.lines).find((line) => line.kind !== "context")?.line ?? 1;
    openAt(line);
  };

  const openAt = (line: number) => {
    if (!onOpenFile) return;
    onOpenFile(edit.path, displayName, { line });
  };

  const replay = (e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    verseEditor?.startReplay(edit);
  };

  return (
    <div className="tool-file-edit-diff">
      <div className={`tool-file-edit-diff-header${expanded ? " tool-file-edit-diff-header--expanded" : ""}`}>
        <button
          type="button"
          className="tool-file-edit-diff-header-toggle"
          onClick={() => setExpanded((v) => !v)}
        >
          <span className="tool-file-edit-diff-header-icon">
            <Icons.File />
          </span>
          <span className="tool-file-edit-diff-header-body">
            <span
              className="tool-file-edit-diff-filename"
              onClick={(e) => {
                if (!onOpenFile) return;
                e.stopPropagation();
                openFirstChange();
              }}
              role={onOpenFile ? "link" : undefined}
              tabIndex={onOpenFile ? 0 : undefined}
            >
              {displayName}
            </span>
            {hasChanges ? (
              <span className="tool-file-edit-diff-stats">
                {edit.linesAdded > 0 && (
                  <span className="tool-file-edit-diff-stat tool-file-edit-diff-stat--add">+{edit.linesAdded}</span>
                )}
                {edit.linesRemoved > 0 && (
                  <span className="tool-file-edit-diff-stat tool-file-edit-diff-stat--remove">-{edit.linesRemoved}</span>
                )}
              </span>
            ) : (
              <span className="tool-file-edit-diff-stats tool-file-edit-diff-stats--unchanged">unchanged</span>
            )}
          </span>
          <span className={`tool-file-edit-diff-chevron${expanded ? " tool-file-edit-diff-chevron--expanded" : ""}`}>
            <Icons.ChevronDown />
          </span>
        </button>
        {verseEditor ? (
          <button
            type="button"
            className="tool-file-edit-diff-replay"
            title="Walk through this edit in the editor"
            aria-label="Walk through this edit in the editor"
            onClick={replay}
          >
            <Icons.Replay />
            <span>Walk through</span>
          </button>
        ) : null}
      </div>

      <div className={`tool-file-edit-diff-collapse${expanded ? " is-open" : ""}`}>
        {summary ? (
        <div className="tool-file-edit-diff-collapse-inner">
          {summary.hunks.length === 0 ? (
            <div className="tool-file-edit-diff-empty">No line changes</div>
          ) : (
            summary.hunks.map((hunk) => (
              <div key={hunk.id} className="tool-file-edit-diff-hunk">
                <pre className="tool-file-edit-diff-code">
                  {hunk.lines.map((line, idx) => (
                    <div
                      key={`${hunk.id}-${idx}`}
                      className={`tool-file-edit-diff-line tool-file-edit-diff-line--${line.kind}${onOpenFile ? " tool-file-edit-diff-line--clickable" : ""}`}
                      onClick={
                        onOpenFile
                          ? (e) => {
                              e.stopPropagation();
                              openAt(line.kind === "remove" ? line.line : line.line);
                            }
                          : undefined
                      }
                      role={onOpenFile ? "button" : undefined}
                      tabIndex={onOpenFile ? 0 : undefined}
                      onKeyDown={
                        onOpenFile
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                e.stopPropagation();
                                openAt(line.line);
                              }
                            }
                          : undefined
                      }
                    >
                      <span className="tool-file-edit-diff-gutter">
                        {line.kind === "remove" ? line.line : line.kind === "add" ? line.line : line.line}
                      </span>
                      <span className="tool-file-edit-diff-prefix">
                        {line.kind === "add" ? "+" : line.kind === "remove" ? "-" : " "}
                      </span>
                      <code className="tool-file-edit-diff-text">{line.text || " "}</code>
                    </div>
                  ))}
                </pre>
              </div>
            ))
          )}
        </div>
        ) : null}
      </div>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Icons } from "../icons/Icons";
import { chatCollapseKey, useChatCollapseScope, useChatCollapseState } from "../hooks/useChatCollapseState";
import type { FileEditData } from "../types/panel";
import { buildFileEditDiff, type FileDiffLine, type FileEditDiffSummary } from "../utils/fileEditDiff";
import { useVerseEditorOptional } from "../verse-editor/VerseEditorProvider";
import { basename } from "../verse-editor/utils/isVerseFile";
import { Modal } from "./Modal";

interface ToolFileEditDiffProps {
  edit: FileEditData;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  /** Start open inline (changeset review, Changes view); chat cards open the diff in a popup. */
  defaultExpanded?: boolean;
  /** Reads the edit with its before/after texts, for an edit that came without them
   *  (`contentOmitted`: a chat page leaves big ones out until the diff is opened). */
  loadFull?: () => Promise<FileEditData | null>;
}

/** Lines per block: the browser skips laying out blocks scrolled out of view, so huge diffs stay smooth. */
const CHUNK_LINES = 200;

function buildDiff(edit: FileEditData): FileEditDiffSummary {
  return buildFileEditDiff(
    edit.path,
    edit.before,
    edit.after,
    edit.linesAdded,
    edit.linesRemoved,
    edit.kind === "create" ? "create" : "write",
  );
}

function chunks(lines: FileDiffLine[]): FileDiffLine[][] {
  const out: FileDiffLine[][] = [];
  for (let k = 0; k < lines.length; k += CHUNK_LINES) out.push(lines.slice(k, k + CHUNK_LINES));
  return out;
}

function DiffLines({ summary, openAt }: { summary: FileEditDiffSummary; openAt?: (line: number) => void }) {
  if (summary.hunks.length === 0) return <div className="tool-file-edit-diff-empty">No line changes</div>;
  return (
    <>
      {summary.hunks.map((hunk) => (
        <div key={hunk.id} className="tool-file-edit-diff-hunk">
          <pre className="tool-file-edit-diff-code">
            {chunks(hunk.lines).map((block, b) => (
              <div key={b} className="tool-file-edit-diff-chunk">
                {block.map((line, idx) => (
                  <div
                    key={idx}
                    className={`tool-file-edit-diff-line tool-file-edit-diff-line--${line.kind}${openAt ? " tool-file-edit-diff-line--clickable" : ""}`}
                    onClick={
                      openAt
                        ? (e) => {
                            e.stopPropagation();
                            openAt(line.line);
                          }
                        : undefined
                    }
                    role={openAt ? "button" : undefined}
                    tabIndex={openAt ? 0 : undefined}
                    onKeyDown={
                      openAt
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
                    <span className="tool-file-edit-diff-gutter">{line.line}</span>
                    <span className="tool-file-edit-diff-prefix">
                      {line.kind === "add" ? "+" : line.kind === "remove" ? "-" : " "}
                    </span>
                    <code className="tool-file-edit-diff-text">{line.text || " "}</code>
                  </div>
                ))}
              </div>
            ))}
          </pre>
        </div>
      ))}
    </>
  );
}

export function ToolFileEditDiff({ edit, onOpenFile, defaultExpanded = false, loadFull }: ToolFileEditDiffProps) {
  const inline = defaultExpanded;
  const collapseScope = useChatCollapseScope();
  const [expanded, setExpanded] = useChatCollapseState(
    chatCollapseKey(collapseScope, inline ? "file-diff-review" : "file-diff", edit.path),
    defaultExpanded,
  );
  const [popup, setPopup] = useState(false);
  const verseEditor = useVerseEditorOptional();
  const shown = inline ? expanded : popup;
  // An edit that came without its texts: read once, when the diff, the file or the
  // walk-through is first asked for. Until then the card needs only its name and counts.
  const [loaded, setLoaded] = useState<FileEditData | null>(null);
  const [loadError, setLoadError] = useState("");
  const pendingRef = useRef<Promise<FileEditData | null> | null>(null);
  const full = edit.contentOmitted ? loaded : edit;
  const ensureFull = (): Promise<FileEditData | null> => {
    if (full) return Promise.resolve(full);
    if (!loadFull) return Promise.resolve(null);
    if (!pendingRef.current) {
      setLoadError("");
      pendingRef.current = loadFull()
        .then((got) => {
          if (got) setLoaded(got);
          else setLoadError("These changes could not be loaded.");
          return got;
        })
        .catch(() => {
          setLoadError("These changes could not be loaded.");
          return null;
        })
        .finally(() => {
          pendingRef.current = null;
        });
    }
    return pendingRef.current;
  };
  useEffect(() => {
    if (shown && !full) void ensureFull();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ensureFull reads the latest props itself
  }, [shown, full]);
  const summary = useMemo(() => (shown && full ? buildDiff(full) : null), [full, shown]);

  const displayName = basename(edit.path);
  const hasChanges = edit.linesAdded > 0 || edit.linesRemoved > 0 || edit.kind === "create";

  const openAt = (line: number) => {
    if (!onOpenFile) return;
    setPopup(false);
    onOpenFile(edit.path, displayName, { line });
  };

  const openFirstChange = () => {
    const open = (got: FileEditData | null) => {
      const diff = got ? (got === full && summary ? summary : buildDiff(got)) : null;
      openAt(diff?.hunks.flatMap((h) => h.lines).find((line) => line.kind !== "context")?.line ?? 1);
    };
    // At once when the texts are here; an edit that came without them is read first.
    if (full) open(full);
    else void ensureFull().then(open);
  };

  const replay = (e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    setPopup(false);
    if (full) verseEditor?.startReplay(full);
    else void ensureFull().then((got) => (got ? verseEditor?.startReplay(got) : undefined));
  };

  const stats = hasChanges ? (
    <span className="tool-file-edit-diff-stats">
      {edit.linesAdded > 0 && <span className="tool-file-edit-diff-stat tool-file-edit-diff-stat--add">+{edit.linesAdded}</span>}
      {edit.linesRemoved > 0 && (
        <span className="tool-file-edit-diff-stat tool-file-edit-diff-stat--remove">-{edit.linesRemoved}</span>
      )}
    </span>
  ) : (
    <span className="tool-file-edit-diff-stats tool-file-edit-diff-stats--unchanged">unchanged</span>
  );

  const replayButton = verseEditor ? (
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
  ) : null;

  return (
    <div className="tool-file-edit-diff">
      <div className={`tool-file-edit-diff-header${inline && expanded ? " tool-file-edit-diff-header--expanded" : ""}`}>
        <button
          type="button"
          className="tool-file-edit-diff-header-toggle"
          title={inline ? undefined : "Show the changes"}
          onClick={() => (inline ? setExpanded((v) => !v) : setPopup(true))}
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
            {stats}
          </span>
          <span className={`tool-file-edit-diff-chevron${inline && expanded ? " tool-file-edit-diff-chevron--expanded" : ""}`}>
            {inline ? <Icons.ChevronDown /> : <Icons.Maximize />}
          </span>
        </button>
        {replayButton}
      </div>

      {inline ? (
        <div className={`tool-file-edit-diff-collapse${expanded ? " is-open" : ""}`}>
          {summary ? (
            <div className="tool-file-edit-diff-collapse-inner">
              <DiffLines summary={summary} openAt={onOpenFile ? openAt : undefined} />
            </div>
          ) : null}
        </div>
      ) : (
        <Modal
          open={popup}
          onClose={() => setPopup(false)}
          title={displayName}
          width={1100}
          className="tool-file-edit-diff-modal"
          bodyClassName="tool-file-edit-diff-modal-body"
        >
          <div className="tool-file-edit-diff-modal-bar">
            <span className="tool-file-edit-diff-modal-path" title={edit.path}>
              {edit.path}
            </span>
            {stats}
            {onOpenFile ? (
              <button type="button" className="tool-file-edit-diff-replay" onClick={openFirstChange}>
                <Icons.File />
                <span>Open file</span>
              </button>
            ) : null}
            {replayButton}
          </div>
          <div className="tool-file-edit-diff-modal-scroll">
            {summary ? (
              <DiffLines summary={summary} openAt={onOpenFile ? openAt : undefined} />
            ) : full ? null : (
              <div className="tool-file-edit-diff-empty" role={loadError ? "alert" : "status"}>
                {loadError || "Loading the changes…"}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

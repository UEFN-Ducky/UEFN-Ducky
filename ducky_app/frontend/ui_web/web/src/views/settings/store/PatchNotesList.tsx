import { useState } from "react";
import { Icons } from "../../../icons/Icons";
import type { DuckyOSStorePatchNote } from "../../../types/panel";
import { formatPatchDate, pageCount, pageSlice, PATCH_NOTES_PAGE_SIZE } from "./storeData";

type Props = {
  notes: DuckyOSStorePatchNote[] | null;
};

function NoteRows({ notes }: { notes: DuckyOSStorePatchNote[] }) {
  return (
    <ol className="ds-changelog">
      {notes.map((v) => {
        const date = formatPatchDate(v.created_at);
        return (
          <li key={`${v.version || ""}-${v.created_at || ""}-${v.changelog || ""}`}>
            <span className="ds-changelog-ver">v{v.version || ""}</span>
            {date ? <span className="ds-changelog-date">{date}</span> : null}
            <p className="ds-changelog-note">{v.changelog || "—"}</p>
          </li>
        );
      })}
    </ol>
  );
}

export function PatchNotesList({ notes }: Props) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);

  if (notes === null) {
    return (
      <>
        <h3 className="ds-panel-title">Patch notes</h3>
        <p className="ds-panel-desc ds-panel-desc--mute">Loading…</p>
      </>
    );
  }
  if (notes.length === 0) {
    return (
      <>
        <h3 className="ds-panel-title">Patch notes</h3>
        <p className="ds-panel-desc ds-panel-desc--mute">No patch notes yet.</p>
      </>
    );
  }

  const latest = notes[0]!;
  const older = notes.slice(1);
  const totalPages = pageCount(older.length, PATCH_NOTES_PAGE_SIZE);
  const slice = pageSlice(older, page, PATCH_NOTES_PAGE_SIZE);

  return (
    <>
      {older.length ? (
        <button
          type="button"
          className="ds-patch-toggle"
          aria-expanded={open}
          onClick={() => {
            setOpen((v) => !v);
            setPage(1);
          }}
        >
          <h3 className="ds-panel-title">Patch notes</h3>
          <span className="ds-patch-toggle-meta">
            {open ? "Hide older" : `${older.length} older`}
            <Icons.ChevronRight />
          </span>
        </button>
      ) : (
        <h3 className="ds-panel-title">Patch notes</h3>
      )}
      <NoteRows notes={[latest]} />
      {open && older.length ? (
        <>
          <NoteRows notes={slice} />
          {totalPages > 1 ? (
            <div className="ds-pagination ds-pagination--panel">
              <button
                type="button"
                className="ds-page-nav"
                aria-label="Previous page"
                disabled={page === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <Icons.ChevronLeft />
              </button>
              {Array.from({ length: totalPages }).map((_, i) => (
                <button
                  key={i}
                  type="button"
                  className={`ds-page${page === i + 1 ? " is-active" : ""}`}
                  aria-current={page === i + 1 ? "page" : undefined}
                  onClick={() => setPage(i + 1)}
                >
                  {i + 1}
                </button>
              ))}
              <button
                type="button"
                className="ds-page-nav"
                aria-label="Next page"
                disabled={page === totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                <Icons.ChevronRight />
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </>
  );
}

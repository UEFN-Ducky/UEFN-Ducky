import { useEffect, useState } from "react";
import { Icons } from "../../../icons/Icons";
import { getApi } from "../../../hooks/usePanelApi";
import type { DuckyOSStorePatchNote } from "../../../types/panel";
import {
  formatPatchDate,
  pageCount,
  pageSlice,
  PATCH_NOTES_PAGE_SIZE,
} from "./storeData";

type Props = {
  slug: string;
};

export function StorePatchNotes({ slug }: Props) {
  const [notes, setNotes] = useState<DuckyOSStorePatchNote[] | null>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    setNotes(null);
    setPage(1);
    const api = getApi();
    if (!api || typeof api.duckyos_store_versions !== "function" || !slug) {
      setNotes([]);
      return () => {
        cancelled = true;
      };
    }
    void api.duckyos_store_versions(slug).then((res) => {
      if (cancelled) return;
      setNotes(Array.isArray(res.versions) ? res.versions : []);
    }).catch(() => {
      if (!cancelled) setNotes([]);
    });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  const rows = notes || [];
  const totalPages = pageCount(rows.length, PATCH_NOTES_PAGE_SIZE);
  const slice = pageSlice(rows, page, PATCH_NOTES_PAGE_SIZE);

  return (
    <div className="ds-panel ds-patch-notes">
      <h3 className="ds-panel-title">Patch notes</h3>
      {notes === null ? (
        <p className="ds-panel-desc ds-panel-desc--mute">Loading…</p>
      ) : slice.length === 0 ? (
        <p className="ds-panel-desc ds-panel-desc--mute">No patch notes yet.</p>
      ) : (
        <>
          <ol className="ds-changelog">
            {slice.map((v) => {
              const date = formatPatchDate(v.created_at);
              return (
                <li key={`${v.version || ""}-${v.created_at || ""}`}>
                  <span className="ds-changelog-ver">v{v.version || ""}</span>
                  {date ? <span className="ds-changelog-date">{date}</span> : null}
                  <p className="ds-changelog-note">{v.changelog || "—"}</p>
                </li>
              );
            })}
          </ol>
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
      )}
    </div>
  );
}

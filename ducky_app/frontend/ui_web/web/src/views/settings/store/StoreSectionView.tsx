import { useEffect, useState, type ReactNode, type RefObject } from "react";
import { Icons } from "../../../icons/Icons";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
import { StoreCard } from "./StoreCard";
import type { StoreItemHandlers } from "./StoreActions";
import {
  pageCount,
  pageSlice,
  PAGE_SIZE_OPTIONS,
  parsePageSizeChoice,
  SCROLL_BATCH,
  SECTION_PAGE_SIZE,
  type CardBusy,
  type PageSizeChoice,
} from "./storeData";

type Props = {
  title: string;
  items: DuckyOSStoreItemDto[];
  /** Concurrent install/update jobs keyed by slug. */
  jobs: Record<string, CardBusy>;
  /** Enable/buy/uninstall (etc.) busy slugs. */
  actionBusy: Record<string, true>;
  handlers: StoreItemHandlers;
  onBack: () => void;
  onOpen: (item: DuckyOSStoreItemDto) => void;
  /** Optional control next to the title (e.g. Update All). */
  headerAction?: ReactNode;
  /** Scroll container of this layer — reset to top on page change. */
  layerRef: RefObject<HTMLDivElement>;
};

/** Full-grid view of one section: page sizes, Show more, or ALL (scroll batches). */
export function StoreSectionView({
  title,
  items,
  jobs,
  actionBusy,
  handlers,
  onBack,
  onOpen,
  headerAction,
  layerRef,
}: Props) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSizeChoice>(SECTION_PAGE_SIZE);
  const [visibleCount, setVisibleCount] = useState(SCROLL_BATCH);
  const infinite = pageSize === "all";
  const perPage = infinite ? SCROLL_BATCH : pageSize;
  const totalPages = pageCount(items.length, perPage);
  const current = infinite
    ? items.slice(0, visibleCount)
    : pageSlice(items, page, perPage);
  const hasMore = infinite
    ? visibleCount < items.length
    : page < totalPages;

  useEffect(() => {
    if (!infinite) {
      layerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [page, pageSize, infinite, layerRef]);

  useEffect(() => {
    if (!infinite && page > totalPages) setPage(totalPages);
  }, [page, totalPages, infinite]);

  useEffect(() => {
    setVisibleCount(SCROLL_BATCH);
    setPage(1);
  }, [items.length, pageSize]);

  // ALL = append the next batch as the layer nears the bottom (never mount everything up front).
  useEffect(() => {
    if (!infinite || !hasMore) return;
    const el = layerRef.current;
    if (!el) return;
    const onScroll = () => {
      if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240) {
        setVisibleCount((n) => Math.min(items.length, n + SCROLL_BATCH));
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, [infinite, hasMore, items.length, layerRef, visibleCount]);

  return (
    <div className="ds-sectionview">
      <div className="ds-viewbar">
        <button type="button" className="ds-back" onClick={onBack}>
          <span className="ds-back-chevron" aria-hidden>
            <Icons.ChevronLeft />
          </span>
          <span>Back</span>
        </button>
        <h2 className="ds-viewbar-title">
          {title}
          <span className="ds-viewbar-count">
            {items.length} item{items.length === 1 ? "" : "s"}
          </span>
          {headerAction ? <span className="ds-viewbar-action">{headerAction}</span> : null}
        </h2>
        <label className="ds-pagesize">
          <span className="ds-pagesize-label">Show</span>
          <select
            className="ds-pagesize-select"
            aria-label="Items to show"
            value={pageSize}
            onChange={(e) => setPageSize(parsePageSizeChoice(e.target.value))}
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            <option value="all">ALL</option>
          </select>
        </label>
      </div>

      <div className="ds-grid ds-grid--section">
        {current.map((item) => {
          const slug = item.slug || "";
          return (
            <StoreCard
              key={slug || item.name || ""}
              item={item}
              busy={jobs[slug] ?? null}
              actionBusy={Boolean(actionBusy[slug] || actionBusy.__local__ || jobs[slug])}
              handlers={handlers}
              onOpen={onOpen}
            />
          );
        })}
      </div>

      {infinite ? (
        <div className="ds-pagination">
          <span className="ds-pagination-range">
            Showing {current.length} of {items.length}
          </span>
          {hasMore ? (
            <button
              type="button"
              className="ds-page-more"
              onClick={() => setVisibleCount((n) => Math.min(items.length, n + SCROLL_BATCH))}
            >
              Show more
            </button>
          ) : null}
        </div>
      ) : totalPages > 1 ? (
        <div className="ds-pagination">
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
    </div>
  );
}

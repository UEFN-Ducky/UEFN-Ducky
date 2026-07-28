import type { ReactNode, Ref } from "react";

type Props = {
  /** Root class (e.g. plans-tab / skills-tab) for settings padding hooks. */
  className?: string;
  detailOpen: boolean;
  detailRendered: boolean;
  /** Shown when detailRendered but detailBody is null. */
  detailPlaceholder?: ReactNode;
  listHeader?: ReactNode;
  listBody: ReactNode;
  listAriaLabel?: string;
  detailHead?: ReactNode;
  detailBody?: ReactNode;
  detailScrollRef?: Ref<HTMLDivElement>;
  /**
   * Drop scroll-area padding so a full-bleed detail body (editor) can align with
   * CatalogDetailHead — body owns its own horizontal inset via --settings-pad-x.
   */
  detailFlush?: boolean;
  notice?: ReactNode;
  /** Extra nodes outside the slide (modals). */
  children?: ReactNode;
};

/**
 * Plans-style master/detail: list slides left, detail slides in from the right.
 */
export function CatalogSlideShell({
  className,
  detailOpen,
  detailRendered,
  detailPlaceholder,
  listHeader,
  listBody,
  listAriaLabel = "Items",
  detailHead,
  detailBody,
  detailScrollRef,
  detailFlush = false,
  notice,
  children,
}: Props) {
  return (
    <div className={`catalog-slide${className ? ` ${className}` : ""}`}>
      {notice}
      <div className={`catalog-slide-body${detailOpen ? " is-detail" : ""}`}>
        <div className="catalog-slide-list-shell">
          <div className="catalog-slide-list-pane" aria-label={listAriaLabel}>
            {listHeader}
            {listBody}
          </div>
        </div>
        <div className="catalog-slide-detail" aria-hidden={!detailOpen}>
          {detailRendered && detailBody != null ? (
            <div className="catalog-slide-detail-inner">
              {detailHead}
              <div
                ref={detailScrollRef}
                className={`catalog-slide-detail-scroll selectable-text${detailFlush ? " is-flush" : ""}`}
              >
                {detailBody}
              </div>
            </div>
          ) : (
            <div className="catalog-slide-detail-placeholder">
              {detailPlaceholder ?? <p>Select an item to view it.</p>}
            </div>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}

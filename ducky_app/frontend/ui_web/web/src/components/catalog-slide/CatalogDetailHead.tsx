import type { ReactNode, Ref } from "react";
import { Icons } from "../../icons/Icons";
import type { CatalogBreadcrumb } from "./types";

type Props = {
  breadcrumbs: CatalogBreadcrumb[];
  onBack: () => void;
  backAriaLabel?: string;
  actions?: ReactNode;
  /** Optional ref on the back button (walkthrough / ui-targets). */
  backButtonRef?: Ref<HTMLButtonElement>;
};

export function CatalogDetailHead({
  breadcrumbs,
  onBack,
  backAriaLabel = "Back",
  actions,
  backButtonRef,
}: Props) {
  return (
    <header className="catalog-slide-detail-head">
      <div className="catalog-slide-detail-head-left">
        <button
          ref={backButtonRef}
          type="button"
          className="catalog-slide-detail-back"
          onClick={onBack}
          aria-label={backAriaLabel}
        >
          <span className="catalog-slide-detail-back-icon" aria-hidden>
            <Icons.ChevronRight />
          </span>
        </button>
        <span className="catalog-slide-detail-divider" aria-hidden />
        <nav className="catalog-slide-breadcrumb" aria-label="Location">
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.id} className="catalog-slide-breadcrumb-chunk">
              {i > 0 ? (
                <span className="catalog-slide-breadcrumb-sep" aria-hidden>
                  /
                </span>
              ) : null}
              {crumb.current || !crumb.onClick ? (
                <span
                  className={`catalog-slide-breadcrumb-link is-current`}
                  aria-current="page"
                >
                  {crumb.label}
                </span>
              ) : (
                <button
                  type="button"
                  className="catalog-slide-breadcrumb-link"
                  onClick={crumb.onClick}
                >
                  {crumb.label}
                </button>
              )}
            </span>
          ))}
        </nav>
      </div>
      {actions ? <div className="catalog-slide-detail-head-actions">{actions}</div> : null}
    </header>
  );
}

import { StoreRow } from "./StoreRow";

/** Placeholder rows shown while the catalog loads (Netflix-style shell). */
export const SKELETON_ROWS = [
  "Gateways",
  "Trending",
  "Skills",
  "Themes",
  "Games",
] as const;

export function StoreHeroSkeleton() {
  return (
    <div className="ds-hero ds-hero--skeleton" aria-hidden>
      <div className="ds-skel ds-skel--hero-tag" />
      <div className="ds-skel ds-skel--hero-title" />
      <div className="ds-skel ds-skel--hero-desc" />
      <div className="ds-skel ds-skel--hero-art" />
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="ds-card ds-card--skeleton" aria-hidden>
      <div className="ds-skel ds-skel--icon" />
      <div className="ds-skel ds-skel--line ds-skel--w70" />
      <div className="ds-skel ds-skel--line ds-skel--w40" />
      <div className="ds-skel ds-skel--line ds-skel--w90" />
      <div className="ds-skel ds-skel--btn" />
    </div>
  );
}

export function StoreSkeletonRows() {
  return (
    <div className="ds-rows" aria-busy="true" aria-label="Loading catalog">
      {SKELETON_ROWS.map((title) => (
        <StoreRow key={title} title={title} onOpenSection={() => undefined}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="ds-row-item">
              <SkeletonCard />
            </div>
          ))}
        </StoreRow>
      ))}
    </div>
  );
}

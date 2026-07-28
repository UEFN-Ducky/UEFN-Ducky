import { Icons } from "../../../icons/Icons";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
import { asLabelList, itemCategories } from "../storeFilters";
import { StoreActions, type StoreItemHandlers } from "./StoreActions";
import { StoreInstallOverlay } from "./StoreInstallOverlay";
import {
  authorLabelFor,
  formatInstalls,
  formatPrice,
  itemInitials,
  needsPurchase,
  type CardBusy,
} from "./storeData";

type Props = {
  item: DuckyOSStoreItemDto | null;
  /** Concurrent install/update jobs keyed by slug. */
  jobs: Record<string, CardBusy>;
  actionBusy: Record<string, true>;
  handlers: StoreItemHandlers;
  onBack: () => void;
};

/** Slide-in detail pane: identity + actions on the left, stats/about/tags on the right. */
export function StoreDetailView({ item, jobs, actionBusy, handlers, onBack }: Props) {
  if (!item) return null;
  const slug = item.slug || "";
  const installBusy = jobs[slug] ?? null;
  const busy = Boolean(actionBusy[slug] || actionBusy.__local__ || installBusy);
  const cats = itemCategories(item);
  const tags = asLabelList(item.tags);
  const includes = item.contributes_summary || [];
  const state = item.state || "available";
  const showUpdateBadge =
    state === "update" && !installBusy && !needsPurchase(item) && !busy;

  return (
    <div className="ds-detail">
      <div className="ds-viewbar">
        <button type="button" className="ds-back" onClick={onBack}>
          <span className="ds-back-chevron" aria-hidden>
            <Icons.ChevronLeft />
          </span>
          <span>Back</span>
        </button>
      </div>

      <div className="ds-detail-cols">
        <aside className={`ds-detail-side${installBusy ? " ds-detail-side--busy" : ""}`}>
          <div className="ds-detail-icon" aria-hidden>
            {item.icon_data_url ? (
              <img src={item.icon_data_url} alt="" draggable={false} />
            ) : (
              <span className="ds-detail-icon-fallback">{itemInitials(item)}</span>
            )}
          </div>
          <h2 className="ds-detail-name">{item.name || slug}</h2>
          <p className="ds-detail-author">{authorLabelFor(item)}</p>
          <div className="ds-detail-side-actions">
            <StoreActions item={item} handlers={handlers} busy={busy} size="detail" />
            {item.repo_url ? (
              <a
                className="ds-link"
                href={item.repo_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                View repository
              </a>
            ) : null}
          </div>
          {installBusy ? <StoreInstallOverlay busy={installBusy} variant="detail" /> : null}
        </aside>

        <div className="ds-detail-main">
          {showUpdateBadge ? (
            <div className="ds-detail-update">
              Update available
              {item.installed_version != null
                ? ` · installed v${item.installed_version} → v${item.latest_version || "?"}`
                : item.latest_version
                  ? ` · v${item.latest_version}`
                  : ""}
            </div>
          ) : null}

          <div className="ds-statbar">
            <div className="ds-stat">
              <span className="ds-stat-label">
                <Icons.Box /> Version
              </span>
              <span className="ds-stat-value">v{item.latest_version || "—"}</span>
            </div>
            <div className="ds-stat">
              <span className="ds-stat-label">
                <Icons.Download /> Installs
              </span>
              <span className="ds-stat-value">
                {typeof item.install_count === "number"
                  ? formatInstalls(item.install_count)
                  : "—"}
              </span>
            </div>
            <div className="ds-stat">
              <span className="ds-stat-label">
                <Icons.Store /> Source
              </span>
              <span className="ds-stat-value">{authorLabelFor(item)}</span>
            </div>
            <div className="ds-stat">
              <span className="ds-stat-label ds-stat-label--price">
                <Icons.Zap /> Price
              </span>
              <span className="ds-stat-value ds-stat-value--price">
                {item.owned ? "Owned" : formatPrice(item)}
              </span>
            </div>
          </div>

          <div className="ds-panel">
            <h3 className="ds-panel-title">
              {item.kind === "skill" ? "About this skill pack" : "About this plugin"}
            </h3>
            <p className="ds-panel-desc">{item.description || "No description."}</p>
          </div>

          <div className="ds-panel">
            <h3 className="ds-panel-title">Tags &amp; categories</h3>
            <div className="ds-pills">
              {cats.map((c) => (
                <span key={`c:${c}`} className="ds-pill ds-pill--accent">
                  {c}
                </span>
              ))}
              {tags.map((t) => (
                <span key={`t:${t}`} className="ds-pill">
                  {t}
                </span>
              ))}
              {includes.map((c) => (
                <span key={`i:${c}`} className="ds-pill ds-pill--blue">
                  includes {c}
                </span>
              ))}
              {state === "installed" || state === "update" ? (
                <span className="ds-pill ds-pill--green">
                  installed{item.enabled != null ? (item.enabled ? " · on" : " · off") : ""}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

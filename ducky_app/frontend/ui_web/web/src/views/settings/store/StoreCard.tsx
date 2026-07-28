import type { KeyboardEvent } from "react";
import { Icons } from "../../../icons/Icons";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
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
  item: DuckyOSStoreItemDto;
  busy: CardBusy | null;
  actionBusy: boolean;
  handlers: StoreItemHandlers;
  onOpen: (item: DuckyOSStoreItemDto) => void;
};

export function StoreCard({ item, busy, actionBusy, handlers, onOpen }: Props) {
  const slug = item.slug || "";
  const state = item.state || "available";
  const working = busy?.phase === "working";
  const done = busy?.phase === "done";
  // Badge only while Update is the pressable action — hide during install overlay / buy-gate.
  const showUpdateBadge = state === "update" && !busy && !needsPurchase(item) && !actionBusy;

  const open = () => onOpen(item);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  return (
    <article
      className={[
        "ds-card",
        showUpdateBadge ? "ds-card--update" : "",
        working ? "ds-card--busy" : "",
        done ? "ds-card--flash" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      role="button"
      tabIndex={0}
      aria-label={item.name || slug}
      onClick={open}
      onKeyDown={onKeyDown}
    >
      {showUpdateBadge ? (
        <div className="ds-card-update-strip">
          Update
          {item.installed_version != null
            ? ` · v${item.installed_version} → v${item.latest_version || "?"}`
            : item.latest_version
              ? ` · v${item.latest_version}`
              : ""}
        </div>
      ) : null}
      <div className="ds-card-head">
        <div className="ds-card-icon" aria-hidden>
          {item.icon_data_url ? (
            <img src={item.icon_data_url} alt="" draggable={false} />
          ) : (
            <span className="ds-card-icon-fallback">{itemInitials(item)}</span>
          )}
        </div>
        <span className={`ds-price-chip${item.owned ? " ds-price-chip--owned" : ""}`}>
          {item.owned ? "Owned" : formatPrice(item)}
        </span>
      </div>
      <div className="ds-card-body">
        <h4 className="ds-card-title">{item.name || slug}</h4>
        <p className="ds-card-author">{authorLabelFor(item)}</p>
        <div className="ds-card-stats">
          <span className="ds-card-stat">
            <Icons.Box />
            <span>v{item.latest_version || "—"}</span>
          </span>
          <span className="ds-card-stat">
            <Icons.Download />
            <span>
              {typeof item.install_count === "number" ? (
                <>
                  {formatInstalls(item.install_count)} <span>installs</span>
                </>
              ) : authorLabelFor(item) === "Local file" ? (
                "local"
              ) : authorLabelFor(item) === "AI-made" ? (
                "AI"
              ) : (
                "—"
              )}
            </span>
          </span>
        </div>
      </div>
      <div className="ds-card-foot">
        <StoreActions item={item} handlers={handlers} busy={actionBusy} size="card" />
      </div>
      {busy ? <StoreInstallOverlay busy={busy} variant="card" /> : null}
    </article>
  );
}

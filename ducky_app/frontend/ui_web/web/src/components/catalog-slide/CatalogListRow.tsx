import type { Ref } from "react";
import { CatalogSourceBadge } from "./CatalogSourceBadge";
import type { CatalogListRowModel, CatalogSource } from "./types";

type Props = CatalogListRowModel & {
  /** Extra class on the <li>. */
  className?: string;
  /** Optional ref on the row root (walkthrough / ui-targets). */
  liRef?: Ref<HTMLLIElement>;
};

export function CatalogListRow({
  title,
  meta,
  overview,
  source,
  icon,
  selected,
  disabled,
  actions,
  onOpen,
  className,
  liRef,
}: Props) {
  return (
    <li
      ref={liRef}
      className={[
        "catalog-slide-row",
        selected ? "is-selected" : "",
        className || "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <button type="button" className="catalog-slide-row-open" onClick={onOpen} disabled={disabled}>
        {icon ? (
          <span className="catalog-slide-row-icon" aria-hidden>
            {icon}
          </span>
        ) : null}
        <span className="catalog-slide-row-body">
          <span className="catalog-slide-row-title-row">
            <span className="catalog-slide-row-title">{title}</span>
            {source ? <CatalogSourceBadge source={source} /> : null}
          </span>
          {meta ? <span className="catalog-slide-row-meta">{meta}</span> : null}
          {overview ? <span className="catalog-slide-row-overview">{overview}</span> : null}
        </span>
      </button>
      {actions ? <div className="catalog-slide-row-actions">{actions}</div> : null}
    </li>
  );
}

export function catalogSourceFromFlags(flags: {
  plugin?: boolean;
  builtin?: boolean;
  custom?: boolean;
  local?: boolean;
}): CatalogSource {
  if (flags.builtin) return "builtin";
  if (flags.plugin) return "plugin";
  if (flags.custom) return "custom";
  return "local";
}

import type { CatalogSource } from "./types";

const LABEL: Record<CatalogSource, string> = {
  plugin: "Plugin",
  local: "Local",
  custom: "Custom",
  builtin: "Built-in",
};

type Props = {
  source: CatalogSource;
  className?: string;
};

export function CatalogSourceBadge({ source, className }: Props) {
  return (
    <span
      className={`catalog-slide-badge catalog-slide-badge--${source}${className ? ` ${className}` : ""}`}
    >
      {LABEL[source]}
    </span>
  );
}

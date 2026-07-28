import type { ReactNode } from "react";

/** Where a catalog row comes from — shown as a list badge. */
export type CatalogSource = "plugin" | "local" | "custom" | "builtin";

export type CatalogBreadcrumb = {
  id: string;
  label: string;
  /** When true, crumb is non-clickable current page. */
  current?: boolean;
  onClick?: () => void;
};

export type CatalogListRowModel = {
  title: string;
  meta?: ReactNode;
  overview?: string;
  source?: CatalogSource;
  icon?: ReactNode;
  selected?: boolean;
  disabled?: boolean;
  actions?: ReactNode;
  onOpen: () => void;
};

export const CATALOG_DETAIL_SLIDE_MS = 280;

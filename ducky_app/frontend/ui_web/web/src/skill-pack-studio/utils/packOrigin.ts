import type { CatalogSource } from "../../components/catalog-slide/types";
import type { PackSummary, SkillFile } from "../model/types";

export type PackOriginBadge = "Created" | "Store" | "Shipped" | "Plugin";

/** Catalog list badge: Plugin / builtin / local (store) / custom (created). */
export function packCatalogSource(
  pack: Pick<PackSummary, "kind" | "source" | "storeSlug" | "origin">,
): CatalogSource {
  const badge = packOriginBadge(pack);
  if (badge === "Plugin") return "plugin";
  if (badge === "Shipped") return "builtin";
  if (badge === "Store") return "local";
  return "custom";
}

export function packOriginBadge(pack: Pick<PackSummary, "kind" | "source" | "storeSlug" | "origin">): PackOriginBadge {
  if (pack.kind === "plugin") return "Plugin";
  if (pack.kind === "bundled") return "Shipped";
  if (pack.kind === "store" || pack.source === "store" || !!pack.storeSlug) return "Store";
  if (pack.origin === "user" || pack.kind === "custom") return "Created";
  return "Created";
}

export function packBadgeClass(badge: PackOriginBadge): string {
  if (badge === "Created") return "sps-origin-badge is-created";
  if (badge === "Store") return "sps-origin-badge is-store";
  if (badge === "Shipped") return "sps-origin-badge is-shipped";
  return "sps-origin-badge is-plugin";
}

export function storeListingSlug(pack: Pick<PackSummary, "storeSlug" | "sourcePluginId">): string {
  return (pack.storeSlug || pack.sourcePluginId || "").trim();
}

export function isUserFile(file: Pick<SkillFile, "origin" | "id">): boolean {
  return file.origin === "user" && file.id !== "core";
}

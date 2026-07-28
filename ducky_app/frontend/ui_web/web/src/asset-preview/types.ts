import type { AssetPreviewResult } from "../types/panel";

export type { AssetPreviewResult };

export function isPreviewableBinary(relativePath: string): boolean {
  const lower = (relativePath || "").toLowerCase();
  return lower.endsWith(".uasset") || lower.endsWith(".umap");
}

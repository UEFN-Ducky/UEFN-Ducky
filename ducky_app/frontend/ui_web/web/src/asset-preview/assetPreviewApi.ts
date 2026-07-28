import { getApi } from "../hooks/usePanelApi";
import type { AssetPreviewResult } from "./types";

export async function fetchAssetPreview(relativePath: string): Promise<AssetPreviewResult> {
  const api = getApi();
  if (!api?.preview_project_asset) {
    throw new Error("Asset preview is not available");
  }
  return api.preview_project_asset(relativePath);
}

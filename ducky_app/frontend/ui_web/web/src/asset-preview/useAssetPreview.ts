import { useEffect, useState } from "react";

import { onApiReady } from "../hooks/onApiReady";

import { useAssetPreviewCache, useAssetPreviewCacheOptional } from "./AssetPreviewCacheContext";
import { fetchAssetPreview } from "./assetPreviewApi";
import type { AssetPreviewResult } from "./types";

export function useAssetPreview(relativePath: string) {
  const { ensurePreview, getEntry } = useAssetPreviewCache();
  const [eager, setEager] = useState<AssetPreviewResult | null>(null);
  const [eagerPath, setEagerPath] = useState(relativePath);
  const [eagerError, setEagerError] = useState<string | null>(null);
  const [eagerLoading, setEagerLoading] = useState(true);

  // Drop stale results immediately when the tab path changes (don't keep showing
  // the previous mesh/texture classification for the new file).
  if (eagerPath !== relativePath) {
    setEagerPath(relativePath);
    setEager(null);
    setEagerError(null);
    setEagerLoading(true);
  }

  useEffect(() => {
    let cancelled = false;
    setEagerLoading(true);
    setEagerError(null);
    const stop = onApiReady((api) => {
      if (!api.preview_project_asset) {
        if (!cancelled) {
          setEagerError("Asset preview is not available");
          setEagerLoading(false);
        }
        return;
      }
      void api
        .preview_project_asset(relativePath)
        .then((result) => {
          if (!cancelled) {
            setEager(result);
            setEagerPath(relativePath);
            setEagerLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setEagerError(err instanceof Error ? err.message : "Preview failed");
            setEagerLoading(false);
          }
        });
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [relativePath]);

  useEffect(() => {
    ensurePreview(relativePath);
  }, [ensurePreview, relativePath]);

  const cached = getEntry(relativePath);
  const eagerMatches = eagerPath === relativePath ? eager : null;
  return {
    result: eagerMatches ?? cached?.result ?? null,
    loading: eagerLoading && !cached?.result,
    error: (eagerPath === relativePath ? eagerError : null) ?? cached?.error ?? null,
  };
}

export function useAssetThumbnail(relativePath: string) {
  const ctx = useAssetPreviewCacheOptional();

  useEffect(() => {
    ctx?.ensurePreview(relativePath);
  }, [ctx, relativePath]);

  const entry = ctx?.getEntry(relativePath);
  return {
    previewUrl: entry?.result?.preview_url,
    loading: entry?.loading ?? false,
    assetClass: entry?.result?.asset_class,
  };
}

export async function prefetchAssetPreview(relativePath: string): Promise<AssetPreviewResult> {
  return fetchAssetPreview(relativePath);
}

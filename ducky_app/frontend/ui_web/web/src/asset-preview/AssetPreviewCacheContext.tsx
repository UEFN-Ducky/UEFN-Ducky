import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { fetchAssetPreview } from "./assetPreviewApi";
import type { AssetPreviewResult } from "./types";

const MAX_CONCURRENT = 3;
const MAX_CACHE_ENTRIES = 80;

interface CacheEntry {
  result?: AssetPreviewResult;
  error?: string;
  loading: boolean;
}

interface AssetPreviewCacheContextValue {
  getEntry: (path: string) => CacheEntry | undefined;
  ensurePreview: (path: string) => void;
  invalidate: (path: string) => void;
}

const AssetPreviewCacheContext = createContext<AssetPreviewCacheContextValue | null>(null);

export function AssetPreviewCacheProvider({ children }: { children: ReactNode }) {
  const [cache, setCache] = useState<Record<string, CacheEntry>>({});
  const inflightRef = useRef(0);
  const queueRef = useRef<string[]>([]);
  const cacheRef = useRef(cache);
  cacheRef.current = cache;

  const trimCache = useCallback((prev: Record<string, CacheEntry>) => {
    const keys = Object.keys(prev);
    if (keys.length <= MAX_CACHE_ENTRIES) return prev;
    const next = { ...prev };
    const excess = keys.length - MAX_CACHE_ENTRIES;
    for (let i = 0; i < excess; i += 1) {
      const k = keys[i];
      if (k && !next[k]?.loading) delete next[k];
    }
    return next;
  }, []);

  const runNext = useCallback(() => {
    while (inflightRef.current < MAX_CONCURRENT && queueRef.current.length > 0) {
      const path = queueRef.current.shift();
      if (!path) break;
      const existing = cacheRef.current[path];
      if (existing?.result || existing?.loading) continue;

      inflightRef.current += 1;
      setCache((prev) => ({
        ...prev,
        [path]: { ...prev[path], loading: true, error: undefined },
      }));

      void fetchAssetPreview(path)
        .then((result) => {
          setCache((prev) => trimCache({
            ...prev,
            [path]: { result, loading: false },
          }));
        })
        .catch((err: unknown) => {
          setCache((prev) => trimCache({
            ...prev,
            [path]: {
              loading: false,
              error: err instanceof Error ? err.message : "Preview failed",
            },
          }));
        })
        .finally(() => {
          inflightRef.current -= 1;
          runNext();
        });
    }
  }, [trimCache]);

  const ensurePreview = useCallback(
    (path: string) => {
      const key = path.replace(/\\/g, "/");
      const existing = cacheRef.current[key];
      if (existing?.result || existing?.loading) return;
      if (queueRef.current.includes(key)) return;
      queueRef.current.push(key);
      runNext();
    },
    [runNext],
  );

  const getEntry = useCallback((path: string) => cache[path.replace(/\\/g, "/")], [cache]);

  const invalidate = useCallback((path: string) => {
    const key = path.replace(/\\/g, "/");
    setCache((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ getEntry, ensurePreview, invalidate }),
    [getEntry, ensurePreview, invalidate],
  );

  return <AssetPreviewCacheContext.Provider value={value}>{children}</AssetPreviewCacheContext.Provider>;
}

export function useAssetPreviewCache(): AssetPreviewCacheContextValue {
  const ctx = useContext(AssetPreviewCacheContext);
  if (!ctx) {
    throw new Error("useAssetPreviewCache must be used within AssetPreviewCacheProvider");
  }
  return ctx;
}

export function useAssetPreviewCacheOptional(): AssetPreviewCacheContextValue | null {
  return useContext(AssetPreviewCacheContext);
}

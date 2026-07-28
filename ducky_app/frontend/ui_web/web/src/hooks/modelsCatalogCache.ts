import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

export interface CatalogModelRow {
  provider: string;
  providerKey: string;
  id: string;
  name: string;
  supportsVision: boolean;
  supportsTools: boolean;
  supportsWebSearch: boolean;
  contextLimit: number;
  priceIn: number | null;
  priceOut: number | null;
  isLocal: boolean;
}

let cachedModels: CatalogModelRow[] | null = null;
let cachedDefaultModel = "";
let loadPromise: Promise<CatalogModelRow[]> | null = null;
/** Bumped on invalidate so in-flight fetches cannot rewrite a cleared catalog. */
let catalogEpoch = 0;
let autoRefreshInstalled = false;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function isModelsCatalogReady(): boolean {
  return cachedModels !== null;
}

export function getCachedModels(): CatalogModelRow[] | null {
  return cachedModels;
}

export function getCachedDefaultModel(): string {
  return cachedDefaultModel;
}

export function subscribeModelsCatalog(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function invalidateModelsCatalog(): void {
  catalogEpoch += 1;
  cachedModels = null;
  cachedDefaultModel = "";
  loadPromise = null;
  emit();
}

/** Drop cache and fetch again (gateway install / key save). */
export async function refreshModelsCatalog(): Promise<CatalogModelRow[]> {
  invalidateModelsCatalog();
  return loadModelsCatalog({ force: true });
}

/** Coalesce bursty Store install/enable pushes into one catalog reload. */
function scheduleModelsCatalogRefresh(): void {
  if (refreshTimer != null) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void refreshModelsCatalog();
  }, 80);
}

/**
 * Keep the composer / Default Model picker in sync when Store gateways are
 * installed, enabled, disabled, or removed — no app restart required.
 */
export function installModelsCatalogAutoRefresh(): void {
  if (autoRefreshInstalled) return;
  autoRefreshInstalled = true;
  installPanelPushBus();
  subscribePanelPush((event) => {
    if (event.type === "uefn_plugins_changed") {
      // Clear immediately so a removed gateway (e.g. OpenAI) disappears from the
      // picker before the debounced refetch finishes — keep-data leaves the key.
      invalidateModelsCatalog();
      scheduleModelsCatalogRefresh();
      return;
    }
    if (event.type === "key_test_done" && event.ok) {
      scheduleModelsCatalogRefresh();
    }
  });
}

export async function loadModelsCatalog(options?: { force?: boolean }): Promise<CatalogModelRow[]> {
  installModelsCatalogAutoRefresh();

  // Sticky empty [] used to hide newly installed gateways until restart —
  // force / invalidation always re-reads key status + providers.
  if (!options?.force && cachedModels !== null) return cachedModels;
  if (!options?.force && loadPromise) return loadPromise;

  const epoch = catalogEpoch;
  loadPromise = (async () => {
    const api = getApi();
    if (!api) return cachedModels ?? [];

    const [keyStatus, settings] = await Promise.all([api.get_key_status(), api.get_settings()]);
    if (epoch !== catalogEpoch) return cachedModels ?? [];

    const fromDefault = (() => {
      const qualified = (settings.default_model || "").trim();
      if (!qualified) return "";
      const idx = qualified.indexOf(":");
      return idx > 0 ? qualified.slice(idx + 1).trim() : "";
    })();
    cachedDefaultModel = fromDefault || settings.agent_model || "";

    // get_key_status only includes enabled Store gateways (keep-data keys stay
    // on disk but are omitted until the gateway is installed again).
    // Key rows may include coding-agent-only secrets; get_models returns [] for those.
    const providersWithKeys = Object.keys(keyStatus || {}).filter(
      (pk) => !!(keyStatus as Record<string, boolean>)[pk],
    );
    const batches = await Promise.all(
      providersWithKeys.map(async (pk) => {
        const force = !!options?.force;
        // Force-refresh hits the network — keep it off the pywebview bridge.
        const rows = force
          ? await (await import("./bridgeJobAsync")).runBridgeJob<
              Awaited<ReturnType<NonNullable<typeof api.get_models>>>
            >("get_models", [pk, true], 120_000)
          : await api.get_models(pk, false);
        return rows.map(
          (row): CatalogModelRow => ({
            provider: row.provider,
            providerKey: pk,
            id: row.id,
            name: row.name,
            supportsVision: !!row.supports_vision,
            supportsTools: !!row.supports_tools,
            supportsWebSearch: !!row.supports_web_search,
            contextLimit: row.context_limit ?? 0,
            priceIn: row.price_in ?? null,
            priceOut: row.price_out ?? null,
            isLocal: !!row.is_local,
          }),
        );
      }),
    );

    if (epoch !== catalogEpoch) return cachedModels ?? [];
    cachedModels = batches.flat();
    emit();
    return cachedModels;
  })();

  try {
    return await loadPromise;
  } catch {
    return cachedModels ?? [];
  } finally {
    loadPromise = null;
  }
}

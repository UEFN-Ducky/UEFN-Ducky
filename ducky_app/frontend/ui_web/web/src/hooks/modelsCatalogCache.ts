import type { ThinkingMenu } from "../components/thinkingMenu";
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
  supportsThinkingEffort?: boolean | null;
  thinkingMenu?: ThinkingMenu | null;
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

/**
 * Bust in-flight loads and refetch. Keeps the last catalog so UIs stay ready
 * (stale-while-revalidate) — nulling used to flash "Loading models…" over
 * active chats on every uefn_plugins_changed.
 */
export function invalidateModelsCatalog(): void {
  catalogEpoch += 1;
  loadPromise = null;
  emit();
}

/** Hard-clear ready state (tests / rare blank). Prefer refreshModelsCatalog. */
export function clearModelsCatalog(): void {
  catalogEpoch += 1;
  cachedModels = null;
  cachedDefaultModel = "";
  loadPromise = null;
  emit();
}

/** Drop cache and fetch again, asking the gateways for fresh lists (key save / Refresh). */
export async function refreshModelsCatalog(): Promise<CatalogModelRow[]> {
  invalidateModelsCatalog();
  return loadModelsCatalog({ force: true });
}

/** Re-read the Python cache only. A `models_updated` push means that cache just changed,
 *  so asking the gateways again would only announce another update: that loop read the
 *  catalog ~3.5 times a second through the WebView bridge and starved chat loads. */
export async function rereadModelsCatalog(): Promise<CatalogModelRow[]> {
  invalidateModelsCatalog();
  return loadModelsCatalog({ force: true, kick: false });
}

let pendingKick = false;

/** Coalesce bursty pushes into one catalog reload (a kick wins over a plain re-read). */
function scheduleModelsCatalogRefresh(kick: boolean): void {
  pendingKick = pendingKick || kick;
  if (refreshTimer != null) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    const withKick = pendingKick;
    pendingKick = false;
    void (withKick ? refreshModelsCatalog() : rereadModelsCatalog());
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
    if (event.type === "models_updated") {
      scheduleModelsCatalogRefresh(false);
      return;
    }
    if (event.type === "uefn_plugins_changed") {
      // Re-read the Python cache (prune already ran; enabling a gateway kicks its own
      // fetch on the Python side). Do not wait on provider APIs.
      scheduleModelsCatalogRefresh(false);
      return;
    }
    if (event.type === "key_test_done" && event.ok) {
      scheduleModelsCatalogRefresh(true);
    }
  });
}

type ApiModelRow = {
  id: string;
  name: string;
  provider: string;
  provider_key?: string;
  supports_vision?: boolean;
  supports_tools?: boolean;
  supports_web_search?: boolean;
  context_limit?: number;
  price_in?: number | null;
  price_out?: number | null;
  is_local?: boolean;
  supports_thinking_effort?: boolean | null;
  thinking_menu?: ThinkingMenu | null;
};

function applyDefaultFromSettings(defaultModel: string, agentModel: string) {
  const qualified = (defaultModel || "").trim();
  if (!qualified) {
    cachedDefaultModel = agentModel || "";
    return;
  }
  const idx = qualified.indexOf(":");
  cachedDefaultModel = (idx > 0 ? qualified.slice(idx + 1).trim() : qualified) || agentModel || "";
}

function mapApiRow(row: ApiModelRow, fallbackKey: string): CatalogModelRow {
  return {
    provider: row.provider,
    providerKey: (row.provider_key || fallbackKey || "").trim(),
    id: row.id,
    name: row.name,
    supportsVision: !!row.supports_vision,
    supportsTools: !!row.supports_tools,
    supportsWebSearch: !!row.supports_web_search,
    contextLimit: row.context_limit ?? 0,
    priceIn: row.price_in ?? null,
    priceOut: row.price_out ?? null,
    isLocal: !!row.is_local,
    supportsThinkingEffort:
      row.supports_thinking_effort == null ? null : !!row.supports_thinking_effort,
    thinkingMenu: row.thinking_menu?.levels?.length ? row.thinking_menu : null,
  };
}

async function fetchCatalogFromApi(force: boolean, kick: boolean): Promise<CatalogModelRow[]> {
  const api = getApi();
  if (!api) return cachedModels ?? [];

  // One cache-only RPC. `kick` asks for a provider fetch on a Python worker, which
  // pushes models_updated only if the lists changed — never runBridgeJob / never wait 120s.
  if (api.get_models_catalog) {
    const res = await api.get_models_catalog(kick);
    applyDefaultFromSettings(res.default_model || "", res.agent_model || "");
    return (res.models || []).map((row) => mapApiRow(row, row.provider_key || ""));
  }

  const [keyStatus, settings] = await Promise.all([api.get_key_status(), api.get_settings()]);
  applyDefaultFromSettings(settings.default_model || "", settings.agent_model || "");
  const providersWithKeys = Object.keys(keyStatus || {}).filter(
    (pk) => !!(keyStatus as Record<string, boolean>)[pk],
  );
  const batches = await Promise.all(
    providersWithKeys.map(async (pk) => {
      const rows = await api.get_models(pk, force);
      return rows.map((row) => mapApiRow(row, pk));
    }),
  );
  return batches.flat();
}

/** `force` re-reads the Python cache instead of the copy here; `kick` (default: same as
 *  force) also asks the gateways for fresh lists. */
export async function loadModelsCatalog(options?: { force?: boolean; kick?: boolean }): Promise<CatalogModelRow[]> {
  installModelsCatalogAutoRefresh();

  // Sticky empty [] used to hide newly installed gateways until restart —
  // force / invalidation always re-reads the Python cache (still instant).
  if (!options?.force && cachedModels !== null) return cachedModels;
  if (loadPromise) return loadPromise;

  const epoch = catalogEpoch;
  const force = !!options?.force;
  const kick = options?.kick ?? force;
  loadPromise = (async () => {
    const rows = await fetchCatalogFromApi(force, kick);
    if (epoch !== catalogEpoch) return cachedModels ?? rows;
    cachedModels = rows;
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

import type { DuckyOSStoreCatalog } from "../types/panel";

const STORAGE_KEY = "uefn-store-catalog-v1";

/** In-memory catalog — survives Settings tab switches within the same panel session. */
let memory: DuckyOSStoreCatalog | null = null;

/** Disk snapshot without icon payloads (base64 blows past localStorage quotas). */
function forDisk(catalog: DuckyOSStoreCatalog): DuckyOSStoreCatalog {
  return {
    ok: catalog.ok,
    error: catalog.error,
    code: catalog.code,
    account: catalog.account,
    audiences: catalog.audiences,
    audienceNotes: catalog.audienceNotes,
    audienceCategoryLabel: catalog.audienceCategoryLabel,
    audienceEmpty: catalog.audienceEmpty,
    items: (catalog.items || []).map((item) => {
      const { icon_data_url: _drop, ...rest } = item;
      return rest;
    }),
  };
}

function readDisk(): DuckyOSStoreCatalog | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DuckyOSStoreCatalog;
    if (!parsed || !Array.isArray(parsed.items)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeDisk(catalog: DuckyOSStoreCatalog): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(forDisk(catalog)));
  } catch {
    /* quota / private mode — memory cache still works */
  }
}

/** Last known good catalog (memory first, then disk). */
export function peekStoreCatalogCache(): DuckyOSStoreCatalog | null {
  if (memory?.items?.length) return memory;
  const disk = readDisk();
  if (disk?.items?.length) {
    memory = disk;
    return disk;
  }
  return memory;
}

/**
 * Remember a catalog fetch. A failed empty refresh keeps the last good copy, but
 * only for the same account: another account's copy (private items) is replaced.
 */
export function rememberStoreCatalog(catalog: DuckyOSStoreCatalog): void {
  const sameAccount = (catalog.account ?? "") === (peekStoreCatalogCache()?.account ?? "");
  if (sameAccount && catalog.ok === false && !(catalog.items || []).length) return;
  memory = catalog;
  writeDisk(catalog);
}

/** Etag of the catalog in `memory` as last read from the app (a disk copy has no icons: none). */
let memoryEtag = "";
let inFlight: Promise<DuckyOSStoreCatalog | null> | null = null;
let fetchedAt = 0;

/** How long a read counts as fresh: the Setup wizard, the badge and Settings open together at boot. */
export const STORE_CATALOG_FRESH_MS = 30_000;

/**
 * The one way to read the Store catalog. Calls at the same time share one read, a read in the
 * last `maxAgeMs` is reused, and a later read sends the held copy's etag, so an unchanged
 * catalog (~230 KB with its icons) is not sent again. Resolves to the catalog, or null when
 * the app has no Store. Startup used to read it twice in a second (the badge and the wizard).
 */
export function fetchStoreCatalog(
  api: { duckyos_store_catalog?: (etag?: string) => Promise<DuckyOSStoreCatalog> } | null | undefined,
  { maxAgeMs = STORE_CATALOG_FRESH_MS, now = Date.now }: { maxAgeMs?: number; now?: () => number } = {},
): Promise<DuckyOSStoreCatalog | null> {
  if (inFlight) return inFlight;
  if (memory && memoryEtag && now() - fetchedAt < maxAgeMs) return Promise.resolve(memory);
  const read = api?.duckyos_store_catalog;
  if (typeof read !== "function") return Promise.resolve(null);
  const held = memory && memoryEtag ? memoryEtag : "";
  inFlight = (async () => {
    try {
      const next = await read.call(api, held || undefined);
      if (next?.unchanged && memory && (next.account ?? "") === (memory.account ?? "")) {
        fetchedAt = now();
        return memory;
      }
      if (next) {
        rememberStoreCatalog(next);
        if (next.ok !== false) {
          memoryEtag = next.etag || "";
          fetchedAt = now();
        }
      }
      return next ?? null;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

/** Account switched (login / logout / expiry): drop every copy, the Store refetches. */
export function clearStoreCatalogCache(): void {
  memoryEtag = "";
  fetchedAt = 0;
  memory = null;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* private mode */
  }
}

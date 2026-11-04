import { beforeEach, describe, expect, it } from "vitest";
import {
  STORE_CATALOG_FRESH_MS,
  clearStoreCatalogCache,
  fetchStoreCatalog,
  peekStoreCatalogCache,
  rememberStoreCatalog,
} from "./storeCatalogCache";

const store = new Map<string, string>();
const ls = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => {
    store.set(k, String(v));
  },
  removeItem: (k: string) => {
    store.delete(k);
  },
  clear: () => store.clear(),
};
Object.defineProperty(globalThis, "localStorage", { value: ls, configurable: true });

describe("storeCatalogCache", () => {
  beforeEach(() => {
    store.clear();
  });

  it("remembers catalog in memory and disk (icons stripped on disk)", () => {
    rememberStoreCatalog({
      ok: true,
      items: [
        {
          slug: "a",
          name: "A",
          icon_data_url: "data:image/png;base64,AAAA",
        },
      ],
    });
    const mem = peekStoreCatalogCache();
    expect(mem?.items?.[0]?.slug).toBe("a");
    expect(mem?.items?.[0]?.icon_data_url).toBe("data:image/png;base64,AAAA");

    const raw = JSON.parse(store.get("uefn-store-catalog-v1") || "{}");
    expect(raw.items[0].slug).toBe("a");
    expect(raw.items[0].icon_data_url).toBeUndefined();
  });

  it("ignores failed empty catalogs", () => {
    rememberStoreCatalog({ ok: true, items: [{ slug: "keep", name: "Keep" }] });
    rememberStoreCatalog({ ok: false, error: "down", items: [] });
    expect(peekStoreCatalogCache()?.items?.[0]?.slug).toBe("keep");
  });
});

describe("fetchStoreCatalog", () => {
  beforeEach(() => {
    store.clear();
    clearStoreCatalogCache();
  });

  function fakeApi() {
    const calls: Array<string | undefined> = [];
    const full = { ok: true, account: "", etag: "v1", items: [{ slug: "a", name: "A", icon_data_url: "x".repeat(1000) }] };
    const api = {
      duckyos_store_catalog: async (etag?: string) => {
        calls.push(etag);
        return etag === "v1" ? { ok: true, unchanged: true, etag: "v1", account: "" } : full;
      },
    };
    return { api, calls, full };
  }

  it("one boot reads the catalog once: callers at the same time share it", async () => {
    const { api, calls } = fakeApi();
    // The badge, the Setup wizard and Settings' gateway icons all ask at startup.
    const [a, b, c] = await Promise.all([fetchStoreCatalog(api), fetchStoreCatalog(api), fetchStoreCatalog(api)]);
    expect(calls).toEqual([undefined]);
    expect(a?.items?.[0]?.slug).toBe("a");
    expect(b).toBe(a);
    expect(c).toBe(a);
    // Asked again within the fresh window: no read at all.
    await fetchStoreCatalog(api);
    expect(calls.length).toBe(1);
  });

  it("a later read sends the etag and keeps the held copy when nothing changed", async () => {
    const { api, calls, full } = fakeApi();
    let t = 1_000;
    await fetchStoreCatalog(api, { now: () => t });
    t += STORE_CATALOG_FRESH_MS + 1;
    const again = await fetchStoreCatalog(api, { now: () => t });
    expect(calls).toEqual([undefined, "v1"]);
    expect(again?.items).toEqual(full.items); // the held catalog, not the small "unchanged" reply
    const forced = await fetchStoreCatalog(api, { maxAgeMs: 0, now: () => t });
    expect(calls).toEqual([undefined, "v1", "v1"]);
    expect(forced?.items?.[0]?.slug).toBe("a");
  });

  it("an account switch forgets the etag: the next read is a full one", async () => {
    const { api, calls } = fakeApi();
    await fetchStoreCatalog(api);
    clearStoreCatalogCache();
    await fetchStoreCatalog(api);
    expect(calls).toEqual([undefined, undefined]);
  });
});

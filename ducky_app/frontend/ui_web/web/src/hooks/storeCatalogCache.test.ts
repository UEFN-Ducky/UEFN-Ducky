import { beforeEach, describe, expect, it } from "vitest";
import { peekStoreCatalogCache, rememberStoreCatalog } from "./storeCatalogCache";

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

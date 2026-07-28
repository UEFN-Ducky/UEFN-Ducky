import { describe, expect, it } from "vitest";
import { isUserFile, packCatalogSource, packOriginBadge, storeListingSlug } from "./packOrigin";

describe("packOriginBadge", () => {
  it("labels store / shipped / plugin / created", () => {
    expect(
      packOriginBadge({ kind: "store", source: "store", storeSlug: "tips", origin: "" }),
    ).toBe("Store");
    expect(
      packOriginBadge({ kind: "custom", source: "store", storeSlug: "tips", origin: "" }),
    ).toBe("Store");
    expect(
      packOriginBadge({ kind: "bundled", source: "", storeSlug: "", origin: "" }),
    ).toBe("Shipped");
    expect(
      packOriginBadge({ kind: "plugin", source: "", storeSlug: "", origin: "" }),
    ).toBe("Plugin");
    expect(
      packOriginBadge({ kind: "custom", source: "", storeSlug: "", origin: "user" }),
    ).toBe("Created");
  });
});

describe("packCatalogSource", () => {
  it("maps origin badges to catalog sources", () => {
    expect(
      packCatalogSource({ kind: "plugin", source: "", storeSlug: "", origin: "" }),
    ).toBe("plugin");
    expect(
      packCatalogSource({ kind: "bundled", source: "", storeSlug: "", origin: "" }),
    ).toBe("builtin");
    expect(
      packCatalogSource({ kind: "store", source: "store", storeSlug: "tips", origin: "" }),
    ).toBe("local");
    expect(
      packCatalogSource({ kind: "custom", source: "", storeSlug: "", origin: "user" }),
    ).toBe("custom");
  });
});

describe("storeListingSlug", () => {
  it("prefers storeSlug then sourcePluginId", () => {
    expect(storeListingSlug({ storeSlug: "tips", sourcePluginId: "materials" })).toBe("tips");
    expect(storeListingSlug({ storeSlug: "", sourcePluginId: "materials" })).toBe("materials");
  });
});

describe("isUserFile", () => {
  it("marks non-core user origin files", () => {
    expect(isUserFile({ id: "my_notes", origin: "user" })).toBe(true);
    expect(isUserFile({ id: "core", origin: "user" })).toBe(false);
    expect(isUserFile({ id: "extra", origin: "store" })).toBe(false);
  });
});

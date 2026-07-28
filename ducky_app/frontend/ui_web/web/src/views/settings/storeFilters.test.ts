import { describe, expect, it } from "vitest";
import {
  categoryLabel,
  filterStoreItems,
  itemCategories,
  OWNED_CATEGORY,
} from "./storeFilters";
import type { DuckyOSStoreItemDto } from "../../types/panel";

const items: DuckyOSStoreItemDto[] = [
  {
    slug: "galaxy",
    kind: "plugin",
    category: "plugins",
    categories: ["themes"],
    tags: ["space", "hud"],
    name: "Galaxy Craft",
    description: "Terran HUD",
  },
  {
    slug: "verse-tips",
    kind: "skill",
    category: "skills",
    categories: ["skills"],
    tags: ["api"],
    name: "Verse Tips",
    description: "Digest helpers",
  },
  {
    slug: "discord",
    kind: "plugin",
    category: "plugins",
    categories: ["plugins"],
    tags: ["chat"],
    name: "Discord",
    description: "Bot bridge",
  },
  {
    slug: "theme-plugin",
    kind: "plugin",
    category: "plugins",
    categories: ["themes", "plugins"],
    tags: [],
    name: "Hybrid",
    description: "Theme with tools",
  },
];

describe("filterStoreItems", () => {
  it("filters by category plugins", () => {
    expect(filterStoreItems(items, { category: "plugins" }).map((i) => i.slug)).toEqual([
      "discord",
      "theme-plugin",
    ]);
  });

  it("filters by category themes", () => {
    expect(filterStoreItems(items, { category: "themes" }).map((i) => i.slug)).toEqual([
      "galaxy",
      "theme-plugin",
    ]);
  });

  it("filters by category skills", () => {
    expect(filterStoreItems(items, { category: "skills" }).map((i) => i.slug)).toEqual([
      "verse-tips",
    ]);
  });

  it("searches name description tags and categories", () => {
    expect(filterStoreItems(items, { q: "hud" }).map((i) => i.slug)).toEqual(["galaxy"]);
    expect(filterStoreItems(items, { q: "digest" }).map((i) => i.slug)).toEqual(["verse-tips"]);
    expect(filterStoreItems(items, { q: "chat" }).map((i) => i.slug)).toEqual(["discord"]);
  });

  it("combines category and search", () => {
    expect(
      filterStoreItems(items, { category: "plugins", q: "bot" }).map((i) => i.slug),
    ).toEqual(["discord"]);
  });

  it("filters by the virtual installed category", () => {
    const withInstalled = items.map((item) =>
      item.slug === "discord" ? { ...item, installed_version: 3 } : item,
    );
    expect(
      filterStoreItems(withInstalled, { category: "installed" }).map((i) => i.slug),
    ).toEqual(["discord"]);
    expect(
      filterStoreItems(withInstalled, { category: "installed", q: "galaxy" }),
    ).toEqual([]);
  });

  it("filters by the virtual owned category for paid purchases only", () => {
    const withOwned: DuckyOSStoreItemDto[] = [
      ...items,
      {
        slug: "pro-pack",
        kind: "plugin",
        categories: ["plugins"],
        name: "Pro Pack",
        paid: true,
        owned: true,
        price_cents: 500,
      },
      {
        slug: "local-free",
        kind: "plugin",
        categories: ["plugins"],
        name: "Local Free",
        paid: false,
        owned: true,
      },
      {
        slug: "unbought",
        kind: "plugin",
        categories: ["plugins"],
        name: "Unbought",
        paid: true,
        owned: false,
        price_cents: 900,
      },
    ];
    expect(
      filterStoreItems(withOwned, { category: OWNED_CATEGORY }).map((i) => i.slug),
    ).toEqual(["pro-pack"]);
    expect(
      filterStoreItems(withOwned, { category: OWNED_CATEGORY, q: "local" }),
    ).toEqual([]);
  });

  it("falls back empty categories to package bucket", () => {
    expect(
      itemCategories({ slug: "x", kind: "plugin", categories: [] }),
    ).toEqual(["plugins"]);
    expect(
      itemCategories({ slug: "y", kind: "skill", categories: [] }),
    ).toEqual(["skills"]);
  });

  it("formats browse category labels", () => {
    expect(categoryLabel("skills")).toBe("Skills");
    expect(categoryLabel("3d")).toBe("3D");
    expect(categoryLabel("mcps")).toBe("MCPs");
    expect(categoryLabel("sound-packs")).toBe("Sound Packs");
  });
});

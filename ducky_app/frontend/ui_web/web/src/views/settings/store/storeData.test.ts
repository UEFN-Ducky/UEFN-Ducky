import { describe, expect, it } from "vitest";
import type { DuckyOSStoreItemDto } from "../../../types/panel";
import {
  authorLabelFor,
  deriveHeroSlides,
  deriveSections,
  formatInstalls,
  formatPrice,
  installProgressPct,
  needsPurchase,
  pageCount,
  pageSlice,
  parsePageSizeChoice,
  SCROLL_BATCH,
  sectionItems,
} from "./storeData";

const items: DuckyOSStoreItemDto[] = [
  {
    slug: "galaxy",
    kind: "plugin",
    categories: ["themes"],
    name: "Galaxy Craft",
    description: "Terran HUD",
    install_count: 13,
    state: "available",
  },
  {
    slug: "translation",
    kind: "plugin",
    categories: ["plugins"],
    name: "Translation",
    description: "Live translate",
    install_count: 28,
    installed_version: 37,
    state: "update",
  },
  {
    slug: "anthropic",
    kind: "plugin",
    categories: ["plugins", "gateways"],
    name: "Anthropic",
    description: "Claude gateway",
    install_count: 0,
    state: "available",
  },
  {
    slug: "verse-tips",
    kind: "skill",
    categories: ["skills"],
    name: "Verse Tips",
    description: "Digest helpers",
    installed_version: 2,
    state: "installed",
  },
  {
    slug: "ducktactoe",
    kind: "plugin",
    categories: ["plugins", "games"],
    name: "Duck-Tac-Toe",
    description: "Board game",
    install_count: 2,
    state: "available",
  },
];

describe("deriveSections", () => {
  it("orders gateways first, then trending, installed, then other category rows", () => {
    const keys = deriveSections(items).map((s) => s.key);
    expect(keys).toEqual(["gateways", "trending", "installed", "skills", "themes", "games"]);
  });

  it("sorts trending by install count desc and skips zero-count items", () => {
    const trending = deriveSections(items).find((s) => s.key === "trending")!;
    expect(trending.items.map((i) => i.slug)).toEqual(["translation", "galaxy", "ducktactoe"]);
  });

  it("collects installed items regardless of state", () => {
    const installed = deriveSections(items).find((s) => s.key === "installed")!;
    expect(installed.items.map((i) => i.slug)).toEqual(["translation", "verse-tips"]);
  });

  it("inserts owned between trending and installed for paid purchases only", () => {
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
        state: "available",
      },
      {
        slug: "local-free",
        kind: "plugin",
        categories: ["plugins"],
        name: "Local Free",
        paid: false,
        owned: true,
        installed_version: 1,
        state: "installed",
      },
    ];
    const keys = deriveSections(withOwned).map((s) => s.key);
    expect(keys.indexOf("owned")).toBeGreaterThan(keys.indexOf("trending"));
    expect(keys.indexOf("owned")).toBeLessThan(keys.indexOf("installed"));
    const owned = deriveSections(withOwned).find((s) => s.key === "owned")!;
    expect(owned.items.map((i) => i.slug)).toEqual(["pro-pack"]);
  });

  it("omits owned section when there are no paid purchases", () => {
    expect(deriveSections(items).find((s) => s.key === "owned")).toBeUndefined();
  });

  it("appends unknown categories title-cased after the fixed rows", () => {
    const withExtra = [
      ...items,
      { slug: "sfx", kind: "plugin", categories: ["sound-packs"], name: "SFX" } as DuckyOSStoreItemDto,
    ];
    const sections = deriveSections(withExtra);
    const extra = sections[sections.length - 1]!;
    expect(extra.key).toBe("sound-packs");
    expect(extra.title).toBe("Sound Packs");
    expect(sections.map((s) => s.key)).not.toContain("plugins");
  });

  it("hides the redundant plugins browse row", () => {
    expect(deriveSections(items).map((s) => s.key)).not.toContain("plugins");
  });

  it("labels acronym categories nicely", () => {
    const withAcronyms = [
      { slug: "meshy", kind: "plugin", categories: ["3d", "mcps"], name: "Meshy" } as DuckyOSStoreItemDto,
    ];
    const keys = deriveSections(withAcronyms).map((s) => ({ key: s.key, title: s.title }));
    expect(keys).toContainEqual({ key: "3d", title: "3D" });
    expect(keys).toContainEqual({ key: "mcps", title: "MCPs" });
  });

  it("returns no sections for an empty catalog", () => {
    expect(deriveSections([])).toEqual([]);
  });
});

describe("sectionItems", () => {
  it("resolves a section by key", () => {
    expect(sectionItems(items, "games").map((i) => i.slug)).toEqual(["ducktactoe"]);
    expect(sectionItems(items, "missing")).toEqual([]);
  });
});

describe("deriveHeroSlides", () => {
  it("puts an available update first, then most-installed, max 3 unique", () => {
    const slides = deriveHeroSlides(items);
    expect(slides).toHaveLength(3);
    expect(slides[0]!.item.slug).toBe("translation");
    expect(slides[0]!.variant).toBe("update");
    expect(slides[1]!.item.slug).toBe("galaxy");
    expect(new Set(slides.map((s) => s.item.slug)).size).toBe(3);
  });

  it("handles catalogs without updates", () => {
    const noUpdate = items.filter((i) => i.state !== "update");
    const slides = deriveHeroSlides(noUpdate);
    expect(slides[0]!.variant).toBe("emerald");
    expect(slides[0]!.item.slug).toBe("galaxy");
  });

  it("returns nothing for an empty catalog", () => {
    expect(deriveHeroSlides([])).toEqual([]);
  });
});

describe("pagination", () => {
  it("computes page counts", () => {
    expect(pageCount(0, 12)).toBe(1);
    expect(pageCount(12, 12)).toBe(1);
    expect(pageCount(13, 12)).toBe(2);
  });

  it("slices and clamps pages", () => {
    const list = Array.from({ length: 30 }, (_, i) => i);
    expect(pageSlice(list, 1, 12)).toHaveLength(12);
    expect(pageSlice(list, 3, 12)).toEqual([24, 25, 26, 27, 28, 29]);
    expect(pageSlice(list, 99, 12)).toEqual([24, 25, 26, 27, 28, 29]);
    expect(pageSlice(list, 0, 12)).toHaveLength(12);
  });

  it("parses page-size choices including ALL", () => {
    expect(parsePageSizeChoice("12")).toBe(12);
    expect(parsePageSizeChoice("50")).toBe(50);
    expect(parsePageSizeChoice("all")).toBe("all");
    expect(parsePageSizeChoice("999")).toBe(12);
    expect(SCROLL_BATCH).toBe(50);
  });

  it("maps install steps to progress percent", () => {
    expect(installProgressPct({ slug: "a", label: "", phase: "working", step: "download" })).toBeGreaterThan(0);
    expect(installProgressPct({ slug: "a", label: "", phase: "working", step: "sync" })).toBeGreaterThan(
      installProgressPct({ slug: "a", label: "", phase: "working", step: "download" }),
    );
    expect(installProgressPct({ slug: "a", label: "", phase: "done", step: "done" })).toBe(100);
    expect(installProgressPct({ slug: "a", label: "fail", phase: "error", step: "download" })).toBe(100);
  });
});

describe("labels", () => {
  it("formats install counts compactly", () => {
    expect(formatInstalls(undefined)).toBe("—");
    expect(formatInstalls(0)).toBe("0");
    expect(formatInstalls(950)).toBe("950");
    expect(formatInstalls(1200)).toBe("1.2k");
    expect(formatInstalls(2000)).toBe("2k");
    expect(formatInstalls(124_000)).toBe("124k");
    expect(formatInstalls(1_500_000)).toBe("1.5M");
  });

  it("derives an author line from the source", () => {
    expect(authorLabelFor({ slug: "a", source: "store" })).toBe("DuckyOS Store");
    expect(authorLabelFor({ slug: "b", source: "local" })).toBe("Local file");
    expect(authorLabelFor({ slug: "ai-pack", source: "ai" })).toBe("AI-made");
    expect(authorLabelFor({ slug: "c", source: "bundled" })).toBe("Bundled");
    expect(authorLabelFor({ slug: "d", kind: "skill" })).toBe("Skill pack");
    expect(authorLabelFor({ slug: "e", kind: "plugin" })).toBe("Plugin");
    expect(
      authorLabelFor({ slug: "physics", kind: "skill", latest_version: "1" }),
    ).toBe("DuckyOS Store");
  });

  it("prices free vs paid", () => {
    expect(formatPrice({ slug: "x" })).toBe("Free");
    expect(formatPrice({ slug: "y", paid: true, price_cents: 500, currency: "usd" })).toContain("5");
    expect(needsPurchase({ slug: "y", paid: true })).toBe(true);
    expect(needsPurchase({ slug: "y", paid: true, owned: true })).toBe(false);
    expect(needsPurchase({ slug: "z" })).toBe(false);
  });
});

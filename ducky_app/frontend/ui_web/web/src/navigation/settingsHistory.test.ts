import { describe, expect, it } from "vitest";
import { sameLocation } from "./NavigationHistoryContext";
import { sameSettingsDrill, sameSettingsLocation, type SettingsNavLocation } from "./settingsHistory";
import { isStoreListUnderDetail } from "./useSettingsHistory";

describe("sameSettingsDrill", () => {
  it("treats missing drills as equal", () => {
    expect(sameSettingsDrill(undefined, undefined)).toBe(true);
    expect(sameSettingsDrill(undefined, { type: "store", section: null, slug: null })).toBe(false);
  });

  it("compares store section + slug", () => {
    expect(
      sameSettingsDrill(
        { type: "store", section: "trending", slug: null },
        { type: "store", section: "trending", slug: null },
      ),
    ).toBe(true);
    expect(
      sameSettingsDrill(
        { type: "store", section: "trending", slug: "galaxy" },
        { type: "store", section: "trending", slug: null },
      ),
    ).toBe(false);
  });

  it("compares memory / skills / mcps drills", () => {
    expect(
      sameSettingsDrill(
        { type: "memory", entryName: "topic" },
        { type: "memory", entryName: "topic" },
      ),
    ).toBe(true);
    expect(
      sameSettingsDrill(
        { type: "memory", entryName: "a" },
        { type: "memory", entryName: "b" },
      ),
    ).toBe(false);
    expect(
      sameSettingsDrill(
        { type: "skills", packId: "p", fileId: null },
        { type: "skills", packId: "p" },
      ),
    ).toBe(true);
    expect(
      sameSettingsDrill(
        { type: "skills", packId: "p", fileId: "f" },
        { type: "skills", packId: "p", fileId: null },
      ),
    ).toBe(false);
    expect(
      sameSettingsDrill(
        { type: "mcps", pluginId: "x", toolName: null },
        { type: "mcps", pluginId: "x" },
      ),
    ).toBe(true);
    expect(
      sameSettingsDrill(
        { type: "mcps", pluginId: "x", toolName: "ping" },
        { type: "mcps", pluginId: "x", toolName: null },
      ),
    ).toBe(false);
  });
});

describe("sameSettingsLocation / sameLocation", () => {
  const storeMain: SettingsNavLocation = { kind: "settings", tab: "Store", name: "Store" };
  const storeTrending: SettingsNavLocation = {
    kind: "settings",
    tab: "Store",
    drill: { type: "store", section: "trending", slug: null },
    name: "Trending",
  };
  const storeDetail: SettingsNavLocation = {
    kind: "settings",
    tab: "Store",
    drill: { type: "store", section: "trending", slug: "galaxy" },
    name: "Galaxy",
  };

  it("distinguishes store main → section → detail", () => {
    expect(sameSettingsLocation(storeMain, storeTrending)).toBe(false);
    expect(sameSettingsLocation(storeTrending, storeDetail)).toBe(false);
    expect(sameSettingsLocation(storeDetail, { ...storeDetail })).toBe(true);
  });

  it("hooks into NavLocation equality", () => {
    expect(sameLocation(storeDetail, storeDetail)).toBe(true);
    expect(sameLocation(storeDetail, storeTrending)).toBe(false);
    expect(sameLocation(storeMain, { kind: "file", path: "/a.verse", name: "a" })).toBe(false);
  });

  it("includes header section tabs", () => {
    const skills: SettingsNavLocation = {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "skills",
      name: "Skills",
    };
    const mcps: SettingsNavLocation = {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "mcps",
      name: "MCPs",
    };
    expect(sameSettingsLocation(skills, mcps)).toBe(false);
    expect(sameSettingsLocation(skills, { ...skills })).toBe(true);
  });
});

describe("isStoreListUnderDetail", () => {
  const storeRoot: SettingsNavLocation = { kind: "settings", tab: "Store", name: "Store" };
  const storeTrendingList: SettingsNavLocation = {
    kind: "settings",
    tab: "Store",
    drill: { type: "store", section: "trending", slug: null },
    name: "Trending",
  };
  const detailMain: SettingsNavLocation = {
    kind: "settings",
    tab: "Store",
    drill: { type: "store", section: null, slug: "blender" },
    name: "Blender",
  };
  const detailSection: SettingsNavLocation = {
    kind: "settings",
    tab: "Store",
    drill: { type: "store", section: "installed", slug: "blender" },
    name: "Blender",
  };

  it("treats Store root as list under a no-section detail", () => {
    expect(isStoreListUnderDetail(storeRoot, detailMain)).toBe(true);
    expect(isStoreListUnderDetail(storeTrendingList, detailMain)).toBe(false);
  });

  it("matches section list under section detail", () => {
    const installed: SettingsNavLocation = {
      kind: "settings",
      tab: "Store",
      drill: { type: "store", section: "installed", slug: null },
      name: "Installed",
    };
    expect(isStoreListUnderDetail(installed, detailSection)).toBe(true);
    expect(isStoreListUnderDetail(storeRoot, detailSection)).toBe(false);
  });

  it("rejects other tabs (Support) so Back does not skip Plugins", () => {
    const support: SettingsNavLocation = { kind: "settings", tab: "Support", name: "Support" };
    expect(isStoreListUnderDetail(support, detailMain)).toBe(false);
  });
});

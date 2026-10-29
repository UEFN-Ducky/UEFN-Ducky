import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_HIDDEN,
  discordHeaderButtonId,
  headerButtonCatalog,
  isHeaderButtonVisible,
  normalizeHeaderVisibility,
  pluginHeaderButtonId,
  readHeaderVisibility,
  withHeaderButtonVisible,
} from "./headerVisibilityStorage";

const mem = new Map<string, string>();

beforeEach(() => {
  mem.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => mem.get(key) ?? null,
    setItem: (key: string, value: string) => {
      mem.set(key, String(value));
    },
    removeItem: (key: string) => {
      mem.delete(key);
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("header visibility", () => {
  it("hides Discord until opted in", () => {
    const snap = readHeaderVisibility();
    expect(snap.hidden).toContain(discordHeaderButtonId());
    expect(isHeaderButtonVisible(snap, "search")).toBe(true);
  });

  it("inherits Discord showInHeader on first run", () => {
    mem.set("uefn-plugin-ui-prefs", JSON.stringify({ discord: { showInHeader: true } }));
    expect(readHeaderVisibility().hidden).not.toContain(discordHeaderButtonId());
  });

  it("does not re-migrate after hidden is saved", () => {
    mem.set("uefn-header-visibility", JSON.stringify({ hidden: [], applied: DEFAULT_HIDDEN }));
    expect(normalizeHeaderVisibility({ hidden: [], applied: DEFAULT_HIDDEN }).hidden).toEqual([]);
    expect(readHeaderVisibility().hidden).toEqual([]);
  });

  it("hides Context, Changes and Snip by default, once: turned back on, they stay on", () => {
    expect(readHeaderVisibility().hidden).toEqual(expect.arrayContaining(DEFAULT_HIDDEN));
    // An existing install's saved snapshot gets them once.
    const migrated = normalizeHeaderVisibility({ hidden: ["search"] });
    expect(migrated).toEqual({ hidden: ["search", ...DEFAULT_HIDDEN], applied: DEFAULT_HIDDEN });
    const contextOn = withHeaderButtonVisible(migrated, "composer:context", true);
    expect(isHeaderButtonVisible(normalizeHeaderVisibility(contextOn), "composer:context")).toBe(true);
    expect(isHeaderButtonVisible(contextOn, "composer:snip")).toBe(false);
  });

  it("toggles a builtin without dropping plugin hides", () => {
    const start = { hidden: [discordHeaderButtonId()], applied: DEFAULT_HIDDEN };
    const hiddenSearch = withHeaderButtonVisible(start, "search", false);
    expect(hiddenSearch.hidden).toEqual([discordHeaderButtonId(), "search"]);
    expect(hiddenSearch.applied).toEqual(DEFAULT_HIDDEN);
    expect(withHeaderButtonVisible(hiddenSearch, "search", true).hidden).toEqual([
      discordHeaderButtonId(),
    ]);
  });

  it("lists builtins then contributed plugin buttons", () => {
    const catalog = headerButtonCatalog([
      { id: "discord", title: "Discord Ducky", plugin_id: "discord" },
      { id: "rainbow", title: "Rainbow", plugin_id: "rainbow" },
    ]);
    expect(catalog.map((row) => row.id)).toEqual([
      "nav",
      "leftSidebar",
      "rightSidebar",
      "search",
      pluginHeaderButtonId("discord", "discord"),
      pluginHeaderButtonId("rainbow", "rainbow"),
    ]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
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
    mem.set("uefn-header-visibility", JSON.stringify({ hidden: [] }));
    expect(normalizeHeaderVisibility({ hidden: [] }).hidden).toEqual([]);
    expect(readHeaderVisibility().hidden).toEqual([]);
  });

  it("toggles a builtin without dropping plugin hides", () => {
    const start = { hidden: [discordHeaderButtonId()] };
    const hiddenSearch = withHeaderButtonVisible(start, "search", false);
    expect(hiddenSearch.hidden).toEqual([discordHeaderButtonId(), "search"]);
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

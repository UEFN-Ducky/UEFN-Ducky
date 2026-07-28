import { describe, expect, it } from "vitest";
import { isOrphanedPluginTab, orphanedPluginTabs } from "./orphanedPluginTabs";
import type { EditorTab } from "../types/panel";

function tab(partial: Partial<EditorTab> & Pick<EditorTab, "id" | "kind">): EditorTab {
  return { name: partial.name || partial.id, ...partial };
}

describe("orphanedPluginTabs", () => {
  it("keeps plugin tabs while their plugin is enabled", () => {
    const tabs = [
      tab({ id: "plugin:browser:browser", kind: "plugin" }),
      tab({ id: "plugin:ducktactoe:game", kind: "plugin" }),
      tab({ id: "file:/a.verse", kind: "file" }),
    ];
    expect(orphanedPluginTabs(tabs, ["browser", "ducktactoe"]).map((t) => t.id)).toEqual([]);
  });

  it("flags browser / any ui.panels tab when that plugin is gone", () => {
    const tabs = [
      tab({ id: "plugin:browser:browser", kind: "plugin" }),
      tab({ id: "plugin:browser:browser~ab12", kind: "plugin", name: "Browser" }),
      tab({ id: "chat:1", kind: "chat" }),
    ];
    expect(orphanedPluginTabs(tabs, []).map((t) => t.id)).toEqual([
      "plugin:browser:browser",
      "plugin:browser:browser~ab12",
    ]);
    expect(orphanedPluginTabs(tabs, ["browser"]).map((t) => t.id)).toEqual([]);
  });

  it("flags discord + verse-translated when their plugins are disabled", () => {
    const enabled = new Set<string>(["browser"]);
    expect(
      isOrphanedPluginTab(tab({ id: "discord:main", kind: "discord" }), enabled),
    ).toBe(true);
    expect(
      isOrphanedPluginTab(
        tab({ id: "verse-translated:es:foo.verse", kind: "verse-translated" }),
        enabled,
      ),
    ).toBe(true);
    expect(
      isOrphanedPluginTab(tab({ id: "plugin:browser:browser", kind: "plugin" }), enabled),
    ).toBe(false);
  });

  it("does not touch core tabs", () => {
    const tabs = [
      tab({ id: "settings:main", kind: "settings" }),
      tab({ id: "chat:1", kind: "chat" }),
      tab({ id: "terminal:1", kind: "terminal" }),
    ];
    expect(orphanedPluginTabs(tabs, []).map((t) => t.id)).toEqual([]);
  });
});

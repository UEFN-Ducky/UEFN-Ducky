import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  defaultDockSnapshot,
  normalizeSnapshot,
  panelsOnSide,
  sidebarPanelCatalog,
  withPanelOnSide,
  withRailEnabled,
} from "./workspaceDockStorage";

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

describe("workspace dock sidebar snapshot", () => {
  it("defaults rails on and hides Discord until opted in", () => {
    const snap = defaultDockSnapshot();
    expect(snap.leftRailEnabled).toBe(true);
    expect(snap.rightRailEnabled).toBe(true);
    expect(snap.hiddenPanels).toContain("groupchat");
    expect(panelsOnSide(snap, "left")).toEqual(["chats", "files"]);
    expect(panelsOnSide(snap, "right")).toEqual(["outline", "history", "tester"]);
  });

  it("keeps an explicit hiddenPanels list (no Discord re-migrate)", () => {
    const snap = normalizeSnapshot({ hiddenPanels: [] }, "main");
    expect(snap.hiddenPanels).toEqual([]);
    expect(panelsOnSide(snap, "left")).toContain("groupchat");
  });

  it("migrates Discord plugin sidebar prefs when hiddenPanels is absent", () => {
    mem.set(
      "uefn-plugin-ui-prefs",
      JSON.stringify({ discord: { showInRightSidebar: true } }),
    );
    const snap = normalizeSnapshot({ version: 1 }, "main");
    expect(snap.hiddenPanels).not.toContain("groupchat");
    expect(snap.panelSide.groupchat).toBe("right");
    expect(panelsOnSide(snap, "right")).toContain("groupchat");
    expect(panelsOnSide(snap, "left")).not.toContain("groupchat");
  });

  it("hides and restores a panel without dropping rail enabled flags", () => {
    let snap = defaultDockSnapshot();
    snap = withPanelOnSide(snap, "outline", null);
    expect(panelsOnSide(snap, "right")).not.toContain("outline");
    expect(snap.panelSide.outline).toBe("right");
    snap = withPanelOnSide(snap, "outline", "left");
    expect(panelsOnSide(snap, "left")).toContain("outline");
    expect(snap.leftRailEnabled).toBe(true);
  });

  it("disables a rail without collapsing it", () => {
    const snap = withRailEnabled(defaultDockSnapshot(), "left", false);
    expect(snap.leftRailEnabled).toBe(false);
    expect(snap.leftRailOpen).toBe(true);
  });

  it("lists built-ins plus contributed plugin dock ids", () => {
    expect(sidebarPanelCatalog([])).toEqual(["chats", "files", "outline", "history"]);
    expect(sidebarPanelCatalog(["tester", "groupchat", "unknown"])).toEqual([
      "chats",
      "files",
      "outline",
      "history",
      "tester",
      "groupchat",
    ]);
  });
});

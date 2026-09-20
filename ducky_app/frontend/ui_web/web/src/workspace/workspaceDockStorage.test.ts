import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const saveWorkspaceDock = vi.fn();
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ save_workspace_dock: saveWorkspaceDock }),
}));

import {
  applyDiskDockSnapshot,
  defaultDockSnapshot,
  dockStorageKey,
  normalizeSnapshot,
  panelsOnSide,
  readDockSnapshot,
  sidebarPanelCatalog,
  withPanelOnSide,
  withRailEnabled,
} from "./workspaceDockStorage";

const mem = new Map<string, string>();

beforeEach(() => {
  mem.clear();
  saveWorkspaceDock.mockReset();
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

  it("keeps disabled rails through normalize and boot hydrate", () => {
    expect(normalizeSnapshot({ leftRailEnabled: false, rightRailEnabled: false }, "main").leftRailEnabled).toBe(
      false,
    );
    expect(normalizeSnapshot({ leftRailEnabled: "false", rightRailOpen: "0" }, "main").leftRailEnabled).toBe(
      false,
    );

    const local = withRailEnabled(withRailEnabled(defaultDockSnapshot(), "left", false), "right", false);
    mem.set(dockStorageKey("main"), JSON.stringify(local));
    // Old AppData predates the kill-switch keys — do not default them back on.
    const hydrated = applyDiskDockSnapshot({ version: 1, leftWidth: 280 }, "main");
    expect(hydrated.leftRailEnabled).toBe(false);
    expect(hydrated.rightRailEnabled).toBe(false);
    expect(applyDiskDockSnapshot({ leftRailEnabled: false, rightRailEnabled: true }, "main").rightRailEnabled).toBe(
      true,
    );
  });

  it("does not write AppData defaults when localStorage is empty", () => {
    const empty = readDockSnapshot("main");
    expect(empty.leftRailEnabled).toBe(true);
    expect(saveWorkspaceDock).not.toHaveBeenCalled();
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

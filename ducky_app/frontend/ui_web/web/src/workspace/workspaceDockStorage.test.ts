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
  withLatestRailSwitches,
  withRailEnabled,
  withSavedRailSwitches,
  saveRailSwitch,
  RAIL_SWITCHES_WINDOW,
} from "./workspaceDockStorage";
import { nextChatLayoutMode } from "../types/panel";

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
    const snap = withRailEnabled({ ...defaultDockSnapshot(), leftRailOpen: true }, "left", false);
    expect(snap.leftRailEnabled).toBe(false);
    expect(snap.leftRailOpen).toBe(true);
  });

  it("the header's left button opens a pane that starts closed on the first click", () => {
    // Fresh install: the layout mode is "full" but the dock has the pane closed.
    expect(nextChatLayoutMode("full", false)).toBe("full");
    expect(nextChatLayoutMode("full", true)).toBe("sidebarHidden");
    expect(nextChatLayoutMode("sidebarHidden", true)).toBe("full");
    expect(nextChatLayoutMode("sidebarHidden", false)).toBe("full");
  });

  it("a fresh install opens with both side panes closed", () => {
    const snap = defaultDockSnapshot();
    expect(snap.leftRailOpen).toBe(false);
    expect(snap.rightRailOpen).toBe(false);
    expect(snap.leftRailEnabled).toBe(true);
    expect(snap.rightRailEnabled).toBe(true);
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
    // A stale "on" saved earlier never switches a rail the user turned off back on.
    expect(applyDiskDockSnapshot({ leftRailEnabled: false, rightRailEnabled: true }, "main").rightRailEnabled).toBe(
      false,
    );
    mem.set(dockStorageKey("main"), JSON.stringify(defaultDockSnapshot()));
    expect(applyDiskDockSnapshot({ rightRailEnabled: false }, "main").rightRailEnabled).toBe(false);  // off on disk stays off
  });

  it("keeps the latest rail switches when the dock saves an older copy", () => {
    mem.set(dockStorageKey("main"), JSON.stringify(withRailEnabled(defaultDockSnapshot(), "right", false)));
    const stale = { ...defaultDockSnapshot(), rightWidth: 333 };  // the dock's copy from before Appearance
    const saved = withLatestRailSwitches(stale, "main");
    expect(saved.rightRailEnabled).toBe(false);
    expect(saved.rightWidth).toBe(333);
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
      "unknown",
    ]);
    expect(sidebarPanelCatalog(["tester", "groupchat", "ollama-live", "NOPE"])).toEqual([
      "chats",
      "files",
      "outline",
      "history",
      "tester",
      "groupchat",
      "ollama-live",
    ]);
  });

  it("puts a contributed plugin dock on a rail after opt-in", () => {
    let snap = defaultDockSnapshot();
    snap = withPanelOnSide(snap, "ollama-live", "left");
    expect(panelsOnSide(snap, "left")).toContain("ollama-live");
    expect(panelsOnSide(snap, "right")).not.toContain("ollama-live");
    snap = withPanelOnSide(snap, "ollama-live", "right");
    expect(panelsOnSide(snap, "right")).toContain("ollama-live");
    expect(panelsOnSide(snap, "left")).not.toContain("ollama-live");
  });
});

describe("Appearance rail switches", () => {
  it("are saved in their own record when switched", () => {
    saveRailSwitch("right", false, "main");
    expect(saveWorkspaceDock).toHaveBeenCalledWith({ window_id: RAIL_SWITCHES_WINDOW, snapshot: { leftRailEnabled: true, rightRailEnabled: false } });
  });

  it("win over any dock copy, including one that says on (a phone or a wiped window)", () => {
    const copy = applyDiskDockSnapshot({ rightRailEnabled: true, leftRailEnabled: true }, "main");
    expect(copy.rightRailEnabled).toBe(true);
    const applied = withSavedRailSwitches(copy, { leftRailEnabled: true, rightRailEnabled: false });
    expect(applied.rightRailEnabled).toBe(false);
    expect(applied.leftRailEnabled).toBe(true);
    // Switched back on in Appearance: that wins too.
    expect(withSavedRailSwitches({ ...copy, rightRailEnabled: false }, { rightRailEnabled: true }).rightRailEnabled).toBe(true);
    // No record yet (first start on this version): the copy stays as it was.
    expect(withSavedRailSwitches(copy, null)).toBe(copy);
    expect(withSavedRailSwitches(copy, {})).toBe(copy);
  });
});

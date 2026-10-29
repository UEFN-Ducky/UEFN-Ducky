// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const created: unknown[] = [];
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    create_conversation: async (...args: unknown[]) => {
      created.push(args);
      return { id: `conv-${created.length}`, title: "Art Studio AI" };
    },
  }),
}));

import {
  clampSize,
  closeDock,
  dockPersonality,
  dockSnapshot,
  getDock,
  isPluginDockChat,
  openDock,
  registerChatLookup,
  sanitizeTheme,
  setDock,
  subscribeDocks,
} from "./pluginChatDockStore";

describe("pluginChatDock", () => {
  beforeEach(() => {
    created.length = 0;
    localStorage.clear();
    registerChatLookup(null);
  });

  it("opens with a plugin-focused chat and reuses it on the next open", async () => {
    const seen: number[] = [];
    const off = subscribeDocks(() => seen.push(1));
    const a = await openDock("plugin:artstudio:main", "artstudio", { key: "doc1", title: "Art Studio AI", brief: "Draw with artstudio_* tools." });
    expect(a).toMatchObject({ open: true, expanded: false, place: "bottom", size: 320, convId: "conv-1", title: "Art Studio AI" });
    const config = (created[0] as unknown[])[3] as { ducky_personality: string; coding_agent: string; title: string };
    expect(config.title).toBe("Art Studio AI");
    expect(config.coding_agent).toBe("ducky");
    expect(config.ducky_personality).toContain('"Art Studio AI" plugin');
    expect(config.ducky_personality).toContain("Draw with artstudio_* tools.");
    expect(isPluginDockChat({ id: "conv-1" })).toBe(true);
    const b = await openDock("plugin:artstudio:main", "artstudio", { key: "doc1", title: "Art Studio AI" });
    expect(b.convId).toBe("conv-1");
    expect(created).toHaveLength(1);
    expect(seen.length).toBeGreaterThanOrEqual(2);
    off();
  });

  it("replaces a remembered chat that was deleted, and takes an explicit conv_id as is", async () => {
    await openDock("t1", "p", { key: "k" });
    registerChatLookup((id) => id !== "conv-1");
    expect((await openDock("t1", "p", { key: "k" })).convId).toBe("conv-2");
    expect((await openDock("t2", "p", { conv_id: "mine-7" })).convId).toBe("mine-7");
    expect(created).toHaveLength(2);
  });

  it("set / close / snapshot, with sizes clamped per place", async () => {
    await openDock("t3", "p", { place: "right", size: 9999, theme: { "--accent": "#ff00aa", "--x": "url(evil)", color: "red" } });
    expect(getDock("t3")).toMatchObject({ place: "right", size: 760, theme: { "--accent": "#ff00aa" } });
    setDock("t3", { expanded: true, place: "bottom", size: 50 });
    expect(getDock("t3")).toMatchObject({ expanded: true, place: "bottom", size: 120 });
    closeDock("t3");
    expect(dockSnapshot(getDock("t3"), true)).toEqual({ open: false, expanded: true, place: "bottom", size: 120, conv_id: "conv-1", running: true });
    expect(dockSnapshot(undefined).open).toBe(false);
  });

  it("sanitizes theme values and personality length", () => {
    expect(sanitizeTheme({ "--card": "var(--bg)", "--bad": "a;b", "--b2": "x}" })).toEqual({ "--card": "var(--bg)" });
    expect(sanitizeTheme(["x"])).toEqual({});
    expect(clampSize("bottom", undefined)).toBe(320);
    expect(dockPersonality("T", "b".repeat(5000)).length).toBe(2000);
  });
});

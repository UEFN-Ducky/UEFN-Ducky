// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useSidebarWidth } from "./useSidebarWidth";
import { useSettingsSidebarWidth } from "../views/settings/useSettingsSidebarWidth";
import { useWorkspaceDockLayout } from "./useWorkspaceDockLayout";
import { dockStorageKey } from "../workspace/workspaceDockStorage";
import { useSidebarPanelLayout } from "./useSidebarPanelLayout";
import { useVerseOutlinePanelLayout } from "./useVerseOutlinePanelLayout";

vi.mock("./onApiReady", () => ({ onApiReady: () => () => {} }));
vi.mock("./usePanelApi", () => ({ getApi: () => null }));
beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([
  ["sidebar", useSidebarWidth, "uefn-panel-sidebar-width", 240],
  ["settings", useSettingsSidebarWidth, "uefn-settings-sidebar-width", 200],
] as const)("persists the final %s delta before React commits the resize", (_name, hook, key, initial) => {
  const { result } = renderHook(hook);
  act(() => {
    result.current.onResize(15);
    result.current.onResize(20);
    result.current.persistWidth();
  });
  expect(result.current.width).toBe(initial + 35);
  expect(localStorage.getItem(key)).toBe(String(initial + 35));
});

it("keeps dock drag updates in memory and saves the exact final width once", () => {
  const { result } = renderHook(() => useWorkspaceDockLayout("main"));
  const write = vi.spyOn(Storage.prototype, "setItem");
  const initial = result.current.leftWidth;
  act(() => {
    for (let width = 250; width < 350; width++) result.current.resizeRailWidth("left", width);
  });
  // Live drag must not commit React — that re-renders the chat every pixel.
  expect(result.current.leftWidth).toBe(initial);
  expect(write).not.toHaveBeenCalled();
  act(() => {
    result.current.resizeRailWidth("left", 360);
    result.current.persistRailWidth();
  });
  expect(result.current.leftWidth).toBe(360);
  expect(JSON.parse(localStorage.getItem(dockStorageKey("main"))!).leftWidth).toBe(360);
  expect(write).toHaveBeenCalledTimes(1);
});

it.each(["left", "right"] as const)("saves the final %s stacked resize once, before React commits", (side) => {
  const { result } = renderHook(() => useWorkspaceDockLayout("main"));
  const write = vi.spyOn(Storage.prototype, "setItem");
  const snapshot = { order: ["chats", "files"] as ("chats" | "files")[], panelHeights: { chats: 300, files: 200 } };
  act(() => {
    result.current.resizeSplit(side, 0, 20, 500, snapshot);
    result.current.resizeSplit(side, 0, 60, 500, snapshot);
    expect(write).not.toHaveBeenCalled();
    result.current.persistSplit();
  });
  const saved = JSON.parse(localStorage.getItem(dockStorageKey("main"))!)[side];
  expect(saved.panelFlex.chats / (saved.panelFlex.chats + saved.panelFlex.files)).toBeCloseTo(0.72);
  expect(write).toHaveBeenCalledTimes(1);
});

it("persists the final legacy sidebar split in the same event", () => {
  const { result } = renderHook(useSidebarPanelLayout);
  act(() => {
    const snapshot = { order: ["chats", "files"] as ("chats" | "files")[], panelHeights: { chats: 300, files: 200 } };
    result.current.resizeSplit(0, 20, 500, snapshot);
    result.current.resizeSplit(0, 60, 500, snapshot);
    result.current.persistSplit();
  });
  expect(Number(localStorage.getItem("uefn-sidebar-panel-split"))).toBeCloseTo(0.72);
});

it("persists the final legacy outline split in the same event", () => {
  const { result } = renderHook(useVerseOutlinePanelLayout);
  act(() => {
    const snapshot = { order: ["outline", "history"] as ("outline" | "history")[], panelHeights: { outline: 300, history: 200 } };
    result.current.resizeSplit(0, 20, 500, snapshot);
    result.current.resizeSplit(0, 60, 500, snapshot);
    result.current.persistSplit();
  });
  expect(Number(localStorage.getItem("uefn-verse-outline-panel-split"))).toBeCloseTo(0.72);
});

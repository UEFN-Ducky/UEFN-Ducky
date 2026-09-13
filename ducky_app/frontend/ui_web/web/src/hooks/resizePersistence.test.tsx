// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useSidebarWidth } from "./useSidebarWidth";
import { useSettingsSidebarWidth } from "../views/settings/useSettingsSidebarWidth";
import { useWorkspaceDockLayout } from "./useWorkspaceDockLayout";
import { dockStorageKey } from "../workspace/workspaceDockStorage";

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
  act(() => {
    for (let width = 250; width < 350; width++) result.current.resizeRailWidth("left", width);
  });
  expect(result.current.leftWidth).toBe(349);
  expect(write).not.toHaveBeenCalled();
  act(() => {
    result.current.resizeRailWidth("left", 360);
    result.current.persistRailWidth();
  });
  expect(JSON.parse(localStorage.getItem(dockStorageKey("main"))!).leftWidth).toBe(360);
  expect(write).toHaveBeenCalledTimes(1);
});

// @vitest-environment jsdom
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PanelApi } from "../types/panel";
import { onApiReady } from "./onApiReady";

type TestWindow = Window & { chrome?: { webview?: unknown }; pywebview?: { api: PanelApi } };
const w = window as TestWindow;

function injectPywebview() {
  const api = {
    get_listener_status: vi.fn().mockResolvedValue({ online: true, version: "1" }),
    get_version: vi.fn().mockResolvedValue("1"),
  } as unknown as PanelApi;
  w.pywebview = { api };
  window.dispatchEvent(new Event("pywebviewready"));
  return api;
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ ok: true, result: null }))));
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  delete w.chrome;
  delete w.pywebview;
  window.history.replaceState(null, "", "/");
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetModules();
});

it("waits for pywebview in the desktop window instead of handing out the HTTP proxy", async () => {
  w.chrome = { webview: {} };
  const ready = vi.fn();
  const stop = onApiReady(ready);
  expect(ready).not.toHaveBeenCalled();

  const api = injectPywebview();
  expect(ready).toHaveBeenCalledTimes(1);
  // getApi() hands out the bridge wrapped (big replies over HTTP); calls still reach pywebview.
  const handed = ready.mock.calls[0][0] as PanelApi;
  await expect(handed.get_version()).resolves.toBe("1");
  expect(api.get_version).toHaveBeenCalledTimes(1);
  expect(fetchSpy).not.toHaveBeenCalled();
  stop();
});

it("still answers at once over HTTP in a phone or tunnel browser", async () => {
  const ready = vi.fn();
  onApiReady(ready);
  expect(ready).toHaveBeenCalledTimes(1);
  const api = ready.mock.calls[0][0] as PanelApi;
  await api.get_version();
  expect(fetchSpy).toHaveBeenCalledTimes(1);
  expect(String(fetchSpy.mock.calls[0][0])).toBe("/__panel_api/get_version");
});

/** This app's WebView2 window showing another of your UEFN Duckies: it never gets pywebview. */
function remoteDuckyWindow() {
  window.history.replaceState(null, "", "/remote-ducky/?direct=ducky&to=key_2&name=Laptop%20Ducky");
  w.chrome = { webview: {} };
}

it("answers at once in the window showing another UEFN Ducky, whose bridge never comes", () => {
  remoteDuckyWindow();
  const ready = vi.fn();
  const stop = onApiReady(ready);
  expect(ready).toHaveBeenCalledTimes(1);
  stop();
});

it("lets the header's sidebar buttons open both rails in the window showing another UEFN Ducky", async () => {
  remoteDuckyWindow();
  const { createElement } = await import("react");
  const { act, renderHook } = await import("@testing-library/react");
  const { WorkspaceDockProvider, useWorkspaceDock } = await import("../workspace/WorkspaceDockContext");
  const { flushDockSnapshotToDisk, persistDockSnapshot, readDockSnapshot } = await import(
    "../workspace/workspaceDockStorage"
  );
  const { useRightRailOpen } = await import("./useRightRailOpen");
  const { result, unmount } = renderHook(() => ({ dock: useWorkspaceDock(), header: useRightRailOpen() }), {
    wrapper: ({ children }: { children: ReactNode }) => createElement(WorkspaceDockProvider, null, children),
  });
  // The other Ducky's saved layout loads first.
  await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
  expect(result.current.dock.leftRailOpen).toBe(false);
  expect(result.current.dock.rightRailOpen).toBe(false);

  act(() => result.current.header.toggleRightRail());
  expect(result.current.dock.rightRailOpen).toBe(true);
  // The left button: App's cycleLayoutMode saves the dock the same way.
  act(() => persistDockSnapshot({ ...readDockSnapshot("main"), leftRailOpen: true }, "main"));
  expect(result.current.dock.leftRailOpen).toBe(true);

  act(() => result.current.header.toggleRightRail());
  expect(result.current.dock.rightRailOpen).toBe(false);
  flushDockSnapshotToDisk("main");
  unmount();
});

it("keeps the desktop listener poll on pywebview for the life of the page", async () => {
  vi.useFakeTimers();
  w.chrome = { webview: {} };
  const { renderHook } = await import("@testing-library/react");
  const { useListenerStatus } = await import("./useListenerStatus");
  const { unmount } = renderHook(() => useListenerStatus(8000));

  await vi.advanceTimersByTimeAsync(1000);
  const api = injectPywebview();
  await vi.advanceTimersByTimeAsync(8000 * 5);

  expect(vi.mocked(api.get_listener_status).mock.calls.length).toBeGreaterThanOrEqual(5);
  expect(fetchSpy).not.toHaveBeenCalled();
  unmount();
});

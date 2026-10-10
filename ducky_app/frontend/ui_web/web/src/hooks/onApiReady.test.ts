// @vitest-environment jsdom
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
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.resetModules();
});

it("waits for pywebview in the desktop window instead of handing out the HTTP proxy", () => {
  w.chrome = { webview: {} };
  const ready = vi.fn();
  const stop = onApiReady(ready);
  expect(ready).not.toHaveBeenCalled();

  const api = injectPywebview();
  expect(ready).toHaveBeenCalledTimes(1);
  expect(ready).toHaveBeenCalledWith(api);
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

// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ListenerStatus } from "../types/panel";

const api = vi.hoisted(() => ({ get_listener_status: vi.fn(), get_version: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./onApiReady", () => ({ onApiReady: (fn: (value: typeof api) => void) => { fn(api); return () => {}; } }));

function online(extra: Partial<ListenerStatus> = {}): ListenerStatus {
  return {
    online: true,
    wedged: false,
    version: "1.2.3",
    status_text: "Connected",
    project_match: true,
    uefn_project_name: "Island",
    plugin_connections: [{ id: "blender", program: "Blender", label: "Blender", online: false, detail: "" }],
    ...extra,
  };
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.get_listener_status.mockReset();
});

it("does not re-render subscribers when a poll returns the same status", async () => {
  let uptime = 100;
  api.get_listener_status.mockImplementation(() => Promise.resolve(online({ uptime_sec: (uptime += 8) })));
  const { useListenerStatus } = await import("./useListenerStatus");
  let renders = 0;
  const { result } = renderHook(() => {
    renders += 1;
    return useListenerStatus(8000);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.online).toBe(true);
  const first = result.current;
  const rendersAfterFirst = renders;

  for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(8000); });

  expect(api.get_listener_status.mock.calls.length).toBeGreaterThanOrEqual(10);
  expect(renders).toBe(rendersAfterFirst);
  expect(result.current).toBe(first);
});

it("keeps the shown uptime current without re-rendering the status subscribers", async () => {
  let uptime = 100;
  api.get_listener_status.mockImplementation(() => Promise.resolve(online({ uptime_sec: (uptime += 8) })));
  const { useListenerStatus, useListenerUptimeSec } = await import("./useListenerStatus");
  let statusRenders = 0;
  renderHook(() => {
    statusRenders += 1;
    return useListenerStatus(8000);
  });
  const uptimeHook = renderHook(() => useListenerUptimeSec());
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const rendersAfterFirst = statusRenders;

  for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(8000); });

  expect(uptimeHook.result.current).toBe(uptime);
  expect(statusRenders).toBe(rendersAfterFirst);
});

it("still re-renders when something the user can see changes", async () => {
  api.get_listener_status.mockResolvedValue(online());
  const { useListenerStatus } = await import("./useListenerStatus");
  const { result } = renderHook(() => useListenerStatus(8000));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.uefn_project_name).toBe("Island");

  api.get_listener_status.mockResolvedValue(online({ uefn_project_name: "Other island", project_match: false }));
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(result.current.uefn_project_name).toBe("Other island");
  expect(result.current.project_match).toBe(false);
});
